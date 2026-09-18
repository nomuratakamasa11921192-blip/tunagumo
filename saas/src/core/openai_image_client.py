"""運営(ツナグモ)自身のOpenAI APIキーで画像を生成・編集するクライアント(2026-09-15、
Higgsfieldから移行。docs/task_openai_migration.md Part B)。

費用負担の方針:
- 画像の生成・編集はOpenAI(運営負担、月間AI予算から差し引く)。
- 動画生成はHiggsfieldのまま(顧客自身のキー、顧客負担)。このモジュールは動画を扱わない。

HiggsfieldClientと同じ引数・同じ戻り値(画像のURL文字列)にしてある。OpenAIの画像APIは
URLではなく画像データ(base64)を返すため、workspace/generated_images/ に保存し、
推測困難なファイル名(uuid4)の相対URL `/api/generated-images/<name>` を返す。
このURLは認証なしで表示できる(<img>タグはAuthorizationヘッダを送れないため)。
Higgsfield時代のCDN URLと同じく「URLを知っている人だけが見られる」扱いになる。

公式ドキュメント: https://developers.openai.com/api/docs/guides/image-generation
"""

import asyncio
import base64
import logging
import random
import re
import uuid
from pathlib import Path

import httpx
from openai import AsyncOpenAI

from src.agent import llm as llm_module
from src.core.safe_http import UnsafeURLError, safe_get
from src.video.paths import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

# 生成は速さ重視、編集は元写真への忠実さ重視のモデルを既定にする(公式ガイドの推奨)。
IMAGE_GENERATE_MODEL = "gpt-image-2.5-flare"
IMAGE_EDIT_MODEL = "gpt-image-2.5-sunburst"

# 単価(USD/百万トークン)。2026-09-15、
# https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst と
# https://developers.openai.com/api/docs/pricing で照合済み(flareも同額)。
# 公式に「1枚あたりの価格」は掲載されていない(トークン課金)ため、APIが返すusageから
# 実コストを計算する。モデルを変える場合はこの単価も合わせて書き換えること。
TEXT_INPUT_PER_MTOK = 5.0
IMAGE_INPUT_PER_MTOK = 8.0
IMAGE_OUTPUT_PER_MTOK = 30.0

GENERATED_IMAGES_DIR = WORKSPACE_ROOT / "generated_images"
GENERATED_IMAGE_URL_PREFIX = "/api/generated-images/"
GENERATED_IMAGE_NAME_PATTERN = re.compile(r"^[0-9a-f]{32}\.jpg$")

# 画像生成は文章より時間がかかる(数十秒〜)ため、LLMより長めに取る。
IMAGE_TIMEOUT_SECONDS = 180.0
MAX_SOURCE_IMAGE_BYTES = 20 * 1024 * 1024
SOURCE_FETCH_TIMEOUT_SECONDS = 20.0
_ALLOWED_SOURCE_CONTENT_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class OpenAIImageError(Exception):
    """顧客にそのまま表示してよい文面のメッセージを持つ例外。"""


def generated_image_path(name: str) -> Path | None:
    """配信用: ファイル名が生成規則どおりの場合だけ実パスを返す(パストラバーサル対策)。"""
    if not GENERATED_IMAGE_NAME_PATTERN.fullmatch(name):
        return None
    return GENERATED_IMAGES_DIR / name


def compute_image_cost_usd(usage) -> float:
    """images APIのusageから実コスト(USD)を計算する。usageが無ければ0を返す
    (呼び出し元でESTIMATED_OPENAI_IMAGE_COST_USDにフォールバックする)。"""
    if usage is None:
        return 0.0
    details = getattr(usage, "input_tokens_details", None)
    image_in = (getattr(details, "image_tokens", 0) or 0) if details else 0
    text_in = (getattr(details, "text_tokens", 0) or 0) if details else (usage.input_tokens or 0)
    output = usage.output_tokens or 0
    return (
        text_in * TEXT_INPUT_PER_MTOK + image_in * IMAGE_INPUT_PER_MTOK + output * IMAGE_OUTPUT_PER_MTOK
    ) / 1_000_000


class OpenAIImageClient:
    def __init__(
        self,
        api_key: str,
        *,
        client: AsyncOpenAI | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # SDK内部のリトライは切り、リトライ方針はsrc/agent/llm.pyと同じループに揃える。
        self._client = client or AsyncOpenAI(api_key=api_key, timeout=IMAGE_TIMEOUT_SECONDS, max_retries=0)
        # テスト用フック(元画像の取得に使う。httpx.MockTransportを渡すと通信しない)。
        self._transport = transport
        # 直前の呼び出しで実際にかかったコスト(USD)。呼び出し元がrecord_costに使う。
        self.last_cost_usd: float = 0.0

    async def _call_with_retry(self, fn, **kwargs):
        last_error: Exception | None = None
        for attempt in range(llm_module.MAX_API_RETRIES + 1):
            try:
                return await fn(**kwargs)
            except llm_module._NO_RETRY_ERRORS as e:
                logger.warning("OpenAI画像APIがリトライ不可のエラーを返しました: %s", e)
                if getattr(e, "code", None) == "moderation_blocked":
                    raise OpenAIImageError(
                        "この内容の画像は作成できませんでした。指示の表現を変えてお試しください。"
                    ) from e
                raise OpenAIImageError("画像の作成に失敗しました。時間をおいて再度お試しください。") from e
            except llm_module._RETRYABLE_ERRORS as e:
                last_error = e
                if attempt >= llm_module.MAX_API_RETRIES:
                    break
                backoff = llm_module.BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 0.5)
                await asyncio.sleep(backoff)
        logger.warning("OpenAI画像APIのリトライが尽きました: %s", last_error)
        raise OpenAIImageError("画像の作成が混み合っています。しばらくしてから再度お試しください。") from last_error

    def _save_first_image(self, response) -> str:
        data = getattr(response, "data", None) or []
        b64 = getattr(data[0], "b64_json", None) if data else None
        if not b64:
            raise OpenAIImageError("画像の作成に失敗しました。時間をおいて再度お試しください。")
        GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}.jpg"
        (GENERATED_IMAGES_DIR / name).write_bytes(base64.b64decode(b64))
        self.last_cost_usd = compute_image_cost_usd(getattr(response, "usage", None))
        return f"{GENERATED_IMAGE_URL_PREFIX}{name}"

    async def generate_image(self, prompt: str, quality: str = "draft") -> str:
        """画像を1枚生成し、表示用URLを返す。quality="draft"は低コストの"low"、
        "high"は作り直し用の"high"で生成する(最上位のxhigh/maxは使わない方針)。"""
        self.last_cost_usd = 0.0
        response = await self._call_with_retry(
            self._client.images.generate,
            model=IMAGE_GENERATE_MODEL,
            prompt=prompt,
            quality="high" if quality == "high" else "low",
            size="auto",
            output_format="jpeg",
            output_compression=85,
        )
        return self._save_first_image(response)

    async def edit_image(self, image_url: str, instruction: str) -> str:
        """既存の画像(物件写真など)を指示文で編集し、表示用URLを返す。

        マスクは使わず画像全体を対象にする(docs/task_openai_migration.md Part B)。
        公式ドキュメントにある通り、GPT Imageの編集は指示ベースで、細部が元写真と
        変わることがある。UI側の「試験提供中」の注記は維持すること。
        """
        self.last_cost_usd = 0.0
        filename, content, content_type = await self._load_source_image(image_url)
        response = await self._call_with_retry(
            self._client.images.edit,
            model=IMAGE_EDIT_MODEL,
            image=(filename, content, content_type),
            prompt=instruction,
            # sunburstはinput_fidelityの指定を400で拒否するため送信しない。
            quality="high",
            size="auto",
            output_format="jpeg",
            output_compression=85,
        )
        return self._save_first_image(response)

    async def _load_source_image(self, image_url: str) -> tuple[str, bytes, str]:
        """編集元の画像を読み込む。自分で生成した画像はディスクから読み、外部URLは
        社内ネットワーク等への不正アクセス(SSRF)を防ぐためsafe_get(公開IPのhttpsのみ、
        リダイレクト先も同じ確認)で取得する。"""
        if image_url.startswith(GENERATED_IMAGE_URL_PREFIX):
            path = generated_image_path(image_url.removeprefix(GENERATED_IMAGE_URL_PREFIX))
            if path is None or not path.is_file():
                raise OpenAIImageError("編集する画像が見つかりませんでした。")
            return path.name, path.read_bytes(), "image/jpeg"

        try:
            res = await safe_get(
                image_url, max_bytes=MAX_SOURCE_IMAGE_BYTES, timeout=SOURCE_FETCH_TIMEOUT_SECONDS, transport=self._transport
            )
        except UnsafeURLError as e:
            raise OpenAIImageError(str(e)) from e
        if res.status_code != 200:
            raise OpenAIImageError("画像を読み込めませんでした。URLをご確認ください。")
        ext = _ALLOWED_SOURCE_CONTENT_TYPES.get(res.content_type)
        if ext is None:
            raise OpenAIImageError("対応している画像形式はJPEG・PNG・WebPです。")
        return f"source.{ext}", res.content, res.content_type
