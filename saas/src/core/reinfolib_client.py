"""国土交通省「不動産情報ライブラリ(reinfolib)」の薄いクライアント。

AI価格査定(5位)・空室対策レポート(10位)・エリア人口動態分析(11位)で使う、取引価格・
人口統計データの取得元。顧客ごとのAPIキーではなく、ツナグモ自身が
https://www.reinfolib.mlit.go.jp/ で取得した1つのAPIキー(無料・公共データ)を
settings.reinfolib_api_key として使う(higgsfield_client.py等の「顧客が実費負担する」
モデルとは異なる。政府公開データの利用に課金は発生しない)。

不動産取引価格情報取得API(XIT001)は公式ドキュメントで確認済み。人口メッシュ統計API
(XKT系)はエンドポイント・パラメータ・レスポンス構造を未検証のまま実装している
(higgsfield_client.pyのIMAGE_EDIT_PATHと同じ扱い)。実際のAPIキー取得後、
公式ドキュメント(https://www.reinfolib.mlit.go.jp/help/apiManual/)と突き合わせて
修正すること。
"""

import httpx

BASE_URL = "https://www.reinfolib.mlit.go.jp/ex-api/external"
TRANSACTION_PRICE_PATH = "/XIT001"
# 未検証: 人口メッシュ統計APIの正式なパス名(公式ドキュメントで要確認)。
POPULATION_MESH_PATH = "/XKT013"
FETCH_TIMEOUT_SECONDS = 15.0


class ReinfolibError(Exception):
    pass


class ReinfolibClient:
    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not api_key:
            raise ReinfolibError("reinfolib APIキーが設定されていません。")
        self._headers = {"Ocp-Apim-Subscription-Key": api_key}
        self._transport = transport

    async def _get(self, path: str, params: dict) -> list[dict]:
        async with httpx.AsyncClient(
            headers=self._headers, timeout=FETCH_TIMEOUT_SECONDS, transport=self._transport
        ) as client:
            try:
                res = await client.get(f"{BASE_URL}{path}", params=params)
            except httpx.HTTPError as e:
                raise ReinfolibError(f"reinfolib APIへの接続に失敗しました: {e}") from e

        if res.status_code >= 400:
            raise ReinfolibError(f"reinfolib APIエラー(status={res.status_code}): {res.text[:300]}")

        try:
            data = res.json()
        except ValueError as e:
            raise ReinfolibError(f"reinfolib APIのレスポンスを解析できませんでした: {e}") from e

        if isinstance(data, dict) and "data" in data:
            return data["data"]
        if isinstance(data, list):
            return data
        raise ReinfolibError("reinfolib APIのレスポンス形式が想定と異なります。")

    async def fetch_transaction_prices(
        self, *, prefecture_code: str, city_code: str, property_type: str | None = None, year: int | None = None
    ) -> list[dict]:
        """不動産取引価格情報取得API(XIT001)。直近の四半期を明示しない場合、
        直近1年分程度が返る想定(公式ドキュメント準拠)。"""
        params = {"area": prefecture_code, "city": city_code}
        if property_type:
            params["priceClassification"] = property_type
        if year:
            params["year"] = year
        return await self._get(TRANSACTION_PRICE_PATH, params)

    async def fetch_population_mesh(self, *, city_code: str) -> list[dict]:
        """人口メッシュ統計API(未検証、モジュールdocstring参照)。"""
        return await self._get(POPULATION_MESH_PATH, {"city": city_code})
