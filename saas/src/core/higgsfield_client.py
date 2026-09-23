"""顧客自身のHiggsfield APIキーで画像を生成する薄いクライアント(Anthropic/OpenAIと
同じ理由で、AI利用の実費は顧客が自分で契約・負担する。ツナグモ自身のHiggsfield
アカウントは使わない)。

Higgsfieldは非同期API(投稿→ポーリング)なので、ここで投稿から完了待ちまでをまとめる。
公式ドキュメント: https://docs.higgsfield.ai/docs
"""

import asyncio

import httpx

BASE_URL = "https://api.higgsfield.ai"
IMAGE_GENERATE_PATH = "/higgsfield-ai/soul/v2/standard"
# 2026-09-23: 旧seedance/v1/liteは新APIでmodel_not_found。公式ドキュメント
# (docs.higgsfield.ai/docs/models)の値に置き換え、費用は顧客負担のため安いモデルを選んだ。
# 物件写真あり: Kling 2.5 Turbo(約$0.042/秒、5秒)。写真なし: Hailuo 2.3(約$0.047/秒、6秒)。
# Seedance 2.0は約$0.93/秒と高いため使わない。
# 高画質は写真の有無にかかわらずKling 2.5 TurboのPro版を使う。
VIDEO_IMAGE_TO_VIDEO_PATH = "/kling-video/v2.5-turbo/standard/image-to-video"
VIDEO_IMAGE_TO_VIDEO_HIGH_PATH = "/kling-video/v2.5-turbo/pro/image-to-video"
VIDEO_TEXT_TO_VIDEO_PATH = "/minimax/hailuo-2.3/standard/text-to-video"
VIDEO_TEXT_TO_VIDEO_HIGH_PATH = "/kling-video/v2.5-turbo/pro/text-to-video"
KLING_DURATION_SECONDS = 5
HAILUO_DURATION_SECONDS = 6
# 2026-09-01: バーチャルステージング(物件写真に家具などを追加する画像編集)用。
# 公式ドキュメントに画像編集系モデルのエンドポイント記載が無く、既存の
# soul/v2/standard・seedance/v1/lite/text-to-videoの命名規則から推測した値。
# 未検証(実際のHiggsfieldキーで一度も成功していない)。失敗する場合は
# HiggsfieldError発生時のレスポンス本文をログで確認し、正しいパス/パラメータ名に
# 直すこと。
IMAGE_EDIT_PATH = "/bytedance/seedream/v5/lite/edit"
POLL_INTERVAL_SECONDS = 2.0
POLL_TIMEOUT_SECONDS = 90.0
VIDEO_POLL_TIMEOUT_SECONDS = 240.0
IMAGE_EDIT_POLL_TIMEOUT_SECONDS = 120.0


class HiggsfieldError(Exception):
    pass


class _ParameterRejected(HiggsfieldError):
    """生成受付時の入力拒否だけ、任意パラメータを外した再送を許可する。"""


class HiggsfieldClient:
    def __init__(
        self, key_id: str, key_secret: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._headers = {
            "Authorization": f"Key {key_id}:{key_secret}",
            "Content-Type": "application/json",
        }
        # テスト用フック(httpx.MockTransportを渡すと実際のネットワークアクセスをしない)。
        # 本番では常に未指定。
        self._transport = transport

    async def _submit_and_wait(
        self, path: str, body: dict, *, poll_timeout: float, failure_label: str
    ) -> str:
        """生成リクエストを投げて完了まで待ち、完成したメディアのURLを返す共通処理。
        画像・動画のどちらも「投稿→ポーリング→URL取得」という同じ形なので共通化している。
        """
        async with httpx.AsyncClient(timeout=30.0, transport=self._transport) as client:
            submit_res = await client.post(
                f"{BASE_URL}{path}",
                headers=self._headers,
                json=body,
            )
            if submit_res.status_code == 401:
                raise HiggsfieldError("Higgsfield APIキーが無効です。設定を確認してください。")
            if submit_res.status_code >= 400:
                error_type = (
                    _ParameterRejected if submit_res.status_code in (400, 422) else HiggsfieldError
                )
                raise error_type(f"Higgsfieldへのリクエストに失敗しました: {submit_res.text}")

            submit_data = submit_res.json()
            status_url = submit_data.get("status_url") or (
                f"{BASE_URL}/requests/{submit_data.get('request_id')}/status"
            )

            elapsed = 0.0
            while elapsed < poll_timeout:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                elapsed += POLL_INTERVAL_SECONDS

                status_res = await client.get(status_url, headers=self._headers)
                if status_res.status_code >= 400:
                    raise HiggsfieldError(f"生成状況の確認に失敗しました: {status_res.text}")

                status_data = status_res.json()
                status = status_data.get("status")

                if status == "completed":
                    # 公式の生成結果はvideo.urlまたはimages[].url。
                    # 従来のレスポンス形式にも引き続き対応する。
                    # https://docs.higgsfield.ai/docs/api-reference/requests/get-request-status
                    video = status_data.get("video") or {}
                    images = status_data.get("images") or []
                    media_url = (
                        video.get("url")
                        or next((item.get("url") for item in images if item.get("url")), None)
                        or (status_data.get("result") or {}).get("url")
                        or status_data.get("video_url")
                        or status_data.get("image_url")
                        or status_data.get("url")
                    )
                    if not media_url:
                        raise HiggsfieldError(f"生成は完了しましたが、{failure_label}のURLが取得できませんでした。")
                    return media_url

                if status in ("failed", "error", "nsfw", "canceled", "cancelled"):
                    raise HiggsfieldError(f"{failure_label}の生成に失敗しました(status={status})。")

            raise HiggsfieldError(f"{failure_label}の生成がタイムアウトしました。しばらくしてからもう一度お試しください。")

    async def generate_image(self, prompt: str, quality: str = "draft") -> str:
        """画像を1枚生成し、完成した画像のURLを返す。顧客のキーが無効な場合や、
        Higgsfield側の生成が失敗した場合はHiggsfieldErrorを送出する。

        quality="draft"(既定)は低い品質ティアで生成し、クレジットを節約する。
        「良ければ高画質で作り直す」導線用にquality="high"を用意している(4K等の
        最上位ティアには上げず、上限は2k程度に留める方針)。
        パラメータ名/値は非公式(公式ドキュメントに明記なし)のため、Higgsfield側が
        拒否した場合はパラメータ無し(元の挙動)にフォールバックする。
        """
        body = {"prompt": prompt, "quality": "2k" if quality == "high" else "1.5k"}
        try:
            return await self._submit_and_wait(
                IMAGE_GENERATE_PATH, body, poll_timeout=POLL_TIMEOUT_SECONDS, failure_label="画像"
            )
        except _ParameterRejected:
            return await self._submit_and_wait(
                IMAGE_GENERATE_PATH,
                {"prompt": prompt},
                poll_timeout=POLL_TIMEOUT_SECONDS,
                failure_label="画像",
            )

    async def generate_video(
        self, prompt: str, quality: str = "draft", reference_image_url: str | None = None
    ) -> str:
        """短い動画を1本生成し、完成した動画のURLを返す。画像より生成に時間がかかるため
        タイムアウトを長めに取っている。顧客のキーが無効な場合や、Higgsfield側の生成が
        失敗した場合はHiggsfieldErrorを送出する。

        reference_image_urlを渡すと、その画像(物件URL取込で見つかった写真など)を
        最初のコマにして動画を作る(image-to-video)。画像URLをHiggsfieldが受け付けない
        場合(非公開URL等)は、同じ画質のtext-to-videoで作り直す。
        モデルと項目名は公式ドキュメントの値(上部の定数を参照)。
        """
        high = quality == "high"
        if high:
            text_path, text_body = VIDEO_TEXT_TO_VIDEO_HIGH_PATH, {
                "prompt": prompt, "duration": KLING_DURATION_SECONDS,
            }
        else:
            text_path, text_body = VIDEO_TEXT_TO_VIDEO_PATH, {
                "prompt": prompt, "duration": HAILUO_DURATION_SECONDS,
            }

        if reference_image_url:
            image_path = VIDEO_IMAGE_TO_VIDEO_HIGH_PATH if high else VIDEO_IMAGE_TO_VIDEO_PATH
            try:
                return await self._submit_and_wait(
                    image_path,
                    {"prompt": prompt, "image_url": reference_image_url, "duration": KLING_DURATION_SECONDS},
                    poll_timeout=VIDEO_POLL_TIMEOUT_SECONDS,
                    failure_label="動画",
                )
            except _ParameterRejected:
                pass

        return await self._submit_and_wait(
            text_path, text_body, poll_timeout=VIDEO_POLL_TIMEOUT_SECONDS, failure_label="動画"
        )

    async def edit_image(self, image_url: str, instruction: str) -> str:
        """既存の画像(物件写真など)を指示文で編集する(家具を追加する、明るくする等の
        バーチャルステージング用途)。完成した画像のURLを返す。

        未検証機能: IMAGE_EDIT_PATHの説明を参照。フィールド名の候補を2通り試すが、
        両方失敗する場合はHiggsfieldError(実際のレスポンス本文を含む)を送出する。
        呼び出し元はこれを「現在ご利用いただけません」といった形で顧客に伝えること。
        """
        candidates: list[dict] = [
            {"prompt": instruction, "image_references": [image_url]},
            {"prompt": instruction, "image_url": image_url},
        ]
        last_error: HiggsfieldError | None = None
        for body in candidates:
            try:
                return await self._submit_and_wait(
                    IMAGE_EDIT_PATH,
                    body,
                    poll_timeout=IMAGE_EDIT_POLL_TIMEOUT_SECONDS,
                    failure_label="画像編集",
                )
            except _ParameterRejected as e:
                last_error = e
        assert last_error is not None
        raise last_error
