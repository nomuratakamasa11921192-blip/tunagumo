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
# lite/text-to-videoを既定にしている: promptだけで動く最小構成のモデルで、
# 画像側のsoul/v2/standardと同じ「まずシンプルに動くこと」を優先した選択。
VIDEO_GENERATE_PATH = "/bytedance/seedance/v1/lite/text-to-video"
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
                raise HiggsfieldError(f"Higgsfieldへのリクエストに失敗しました: {submit_res.text}")

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
                    # 公開ドキュメントにcompleted時の正確なフィールド名の記載がなかったため、
                    # ありそうな候補を順に試している。初回の実利用時に実際のレスポンスを
                    # ログで確認し、正しいフィールド名に絞り込むこと。
                    media_url = (
                        status_data.get("result", {}).get("url")
                        or status_data.get("video_url")
                        or status_data.get("image_url")
                        or status_data.get("url")
                    )
                    if not media_url:
                        raise HiggsfieldError(f"生成は完了しましたが、{failure_label}のURLが取得できませんでした。")
                    return media_url

                if status in ("failed", "error", "cancelled"):
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
        except HiggsfieldError:
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

        quality="draft"(既定)はパラメータを付けず、モデル側の既定解像度(このlite
        モデルでは720p程度)のまま生成してクレジットを節約する。quality="high"は
        1080pを明示指定する(4K等の最上位ティアは要求しない方針)。

        reference_image_urlを渡すと、その画像(物件URL取込で見つかった写真など)を
        起点に動画を作る(start_image)。パラメータ名/値はいずれも非公式(公式
        ドキュメントに明記なし)のため、Higgsfield側が拒否した場合は指定を段階的に
        外しながら再試行し、最終的にプロンプトのみでのフォールバックまで行う。
        """
        full_body: dict = {"prompt": prompt}
        if reference_image_url:
            full_body["start_image"] = reference_image_url
        if quality == "high":
            full_body["resolution"] = "1080p"

        # 一番具体的な指定から、段階的にパラメータを外しながら試す。
        # 最後は必ずプロンプトのみ(元の挙動)になるようにする。重複は除く。
        candidates: list[dict] = [full_body]
        if reference_image_url:
            candidates.append({"prompt": prompt, **({"resolution": "1080p"} if quality == "high" else {})})
        candidates.append({"prompt": prompt})
        attempts: list[dict] = []
        for c in candidates:
            if c not in attempts:
                attempts.append(c)

        last_error: HiggsfieldError | None = None
        for attempt_body in attempts:
            try:
                return await self._submit_and_wait(
                    VIDEO_GENERATE_PATH,
                    attempt_body,
                    poll_timeout=VIDEO_POLL_TIMEOUT_SECONDS,
                    failure_label="動画",
                )
            except HiggsfieldError as e:
                last_error = e
        assert last_error is not None
        raise last_error

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
            except HiggsfieldError as e:
                last_error = e
        assert last_error is not None
        raise last_error
