"""国土交通省「不動産情報ライブラリ(reinfolib)」の薄いクライアント。

AI価格査定(5位)・空室対策レポート(10位)・エリア人口動態分析(11位)で使う、取引価格・
人口統計データの取得元。顧客ごとのAPIキーではなく、ツナグモ自身が
https://www.reinfolib.mlit.go.jp/ で取得した1つのAPIキー(無料・公共データ)を
settings.reinfolib_api_key として使う(政府公開データの利用に課金は発生しない)。

2026-09-15、公式APIマニュアル(https://www.reinfolib.mlit.go.jp/help/apiManual/)と
突き合わせて修正した。それまでの実装は必須パラメータ(year/quarter、タイル座標)を
送っておらず、実際のAPIでは必ずエラーになる状態だった。
- XIT001 不動産価格(取引価格・成約価格): year・quarterが必須、area/city/stationのどれか1つ
- XIT002 都道府県内市区町村一覧: areaが必須。{"data": [{"id", "name"}]}
- XKT013 将来推計人口250mメッシュ: response_format・z(11〜15)・x・yが必須(XYZタイル)
  市区町村コードでは引けないため、国土地理院の住所検索API(無料・キー不要)で市区町村の
  位置を求め、周辺のタイルを取得して行政区域コード(SHICODE)で絞り込む。
【未検証】APIキー未取得のため、実際のレスポンスでの動作確認はまだ。キー取得後に確認すること。
"""

import asyncio
import math
from datetime import date

import httpx

BASE_URL = "https://www.reinfolib.mlit.go.jp/ex-api/external"
TRANSACTION_PRICE_PATH = "/XIT001"
MUNICIPALITIES_PATH = "/XIT002"
POPULATION_MESH_PATH = "/XKT013"
GSI_ADDRESS_SEARCH_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch"
FETCH_TIMEOUT_SECONDS = 15.0

# 取引価格は四半期ごとに公表され、直近の四半期はまだ公開されていないことが多い。
# 前の四半期から遡り、データのある四半期をTRANSACTION_QUARTERS_WANTED個集める
# (最大TRANSACTION_QUARTERS_MAX_LOOKBACK四半期まで遡る)。
TRANSACTION_QUARTERS_WANTED = 4
TRANSACTION_QUARTERS_MAX_LOOKBACK = 8

# z=11は1タイルがおよそ20km四方。市区町村の中心のタイルと周囲8枚(計9枚)を取得すれば、
# ほとんどの市区町村の範囲をカバーできる(広大な市町村は一部が欠ける場合がある)。
POPULATION_TILE_ZOOM = 11


class ReinfolibError(Exception):
    pass


def previous_quarters(today: date, count: int) -> list[tuple[int, int]]:
    """todayの属する四半期の1つ前から遡って、(年, 四半期)をcount個返す。"""
    year, quarter = today.year, (today.month - 1) // 3 + 1
    result = []
    for _ in range(count):
        quarter -= 1
        if quarter == 0:
            year, quarter = year - 1, 4
        result.append((year, quarter))
    return result


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    """経度・緯度をXYZタイル座標に変換する(Webメルカトル、標準的な計算式)。"""
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


class ReinfolibClient:
    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not api_key:
            raise ReinfolibError("reinfolib APIキーが設定されていません。")
        self._headers = {"Ocp-Apim-Subscription-Key": api_key}
        self._transport = transport

    async def _request_json(self, url: str, params: dict, *, headers: dict | None = None):
        # 公式マニュアルの通り応答はgzipだが、httpxはContent-Encodingに従い自動で展開する。
        async with httpx.AsyncClient(headers=headers, timeout=FETCH_TIMEOUT_SECONDS, transport=self._transport) as client:
            try:
                res = await client.get(url, params=params)
            except httpx.HTTPError as e:
                raise ReinfolibError("データの取得先に接続できませんでした。時間をおいて再度お試しください。") from e
        return res

    async def _get(self, path: str, params: dict, *, allow_not_found: bool = False) -> list[dict]:
        res = await self._request_json(f"{BASE_URL}{path}", params, headers=self._headers)
        if allow_not_found and res.status_code == 404:
            return []
        if res.status_code >= 400:
            raise ReinfolibError(f"不動産情報ライブラリからデータを取得できませんでした(status={res.status_code})。")
        try:
            data = res.json()
        except ValueError as e:
            raise ReinfolibError("不動産情報ライブラリの応答を読み取れませんでした。") from e

        if isinstance(data, dict) and "data" in data:
            return data["data"] or []
        if isinstance(data, dict) and "features" in data:  # GeoJSON応答(XKT系)
            return [f.get("properties") or {} for f in data["features"] or []]
        if isinstance(data, list):
            return data
        raise ReinfolibError("不動産情報ライブラリの応答形式が想定と異なります。")

    async def fetch_transaction_prices(
        self,
        *,
        city_code: str,
        price_classification: str | None = None,
        today: date | None = None,
    ) -> list[dict]:
        """不動産価格情報(XIT001)を、データのある直近の四半期から集めて返す。
        price_classification: "01"=取引価格のみ、"02"=成約価格のみ、None=両方。"""
        records: list[dict] = []
        quarters_with_data = 0
        for year, quarter in previous_quarters(today or date.today(), TRANSACTION_QUARTERS_MAX_LOOKBACK):
            params = {"year": year, "quarter": quarter, "city": city_code}
            if price_classification:
                params["priceClassification"] = price_classification
            rows = await self._get(TRANSACTION_PRICE_PATH, params, allow_not_found=True)
            if rows:
                records.extend(rows)
                quarters_with_data += 1
                if quarters_with_data >= TRANSACTION_QUARTERS_WANTED:
                    break
        return records

    async def fetch_municipalities(self, *, prefecture_code: str) -> list[dict]:
        """都道府県内の市区町村一覧(XIT002)。[{"id": "11214", "name": "春日部市"}, ...]"""
        return await self._get(MUNICIPALITIES_PATH, {"area": prefecture_code})

    async def locate(self, address: str) -> tuple[float, float]:
        """国土地理院の住所検索APIで、住所(例: "埼玉県春日部市")の経度・緯度を求める。"""
        res = await self._request_json(GSI_ADDRESS_SEARCH_URL, {"q": address})
        try:
            features = res.json() if res.status_code == 200 else []
            lon, lat = features[0]["geometry"]["coordinates"][:2]
        except (ValueError, LookupError, TypeError) as e:
            raise ReinfolibError("市区町村の位置を特定できませんでした。") from e
        return float(lon), float(lat)

    async def fetch_population_mesh(self, *, city_code: str, address: str) -> list[dict]:
        """将来推計人口250mメッシュ(XKT013)のうち、指定した市区町村に属するメッシュを返す。"""
        lon, lat = await self.locate(address)
        cx, cy = lonlat_to_tile(lon, lat, POPULATION_TILE_ZOOM)
        tiles = [(cx + dx, cy + dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
        results = await asyncio.gather(
            *(
                self._get(
                    POPULATION_MESH_PATH,
                    {"response_format": "geojson", "z": POPULATION_TILE_ZOOM, "x": x, "y": y},
                    allow_not_found=True,
                )
                for x, y in tiles
            )
        )
        meshes: dict[str, dict] = {}
        for rows in results:
            for row in rows:
                if str(row.get("SHICODE", "")) == city_code:
                    # タイル境界をまたぐメッシュが重複して返る場合に備え、MESH_IDで重複を除く
                    meshes[str(row.get("MESH_ID") or len(meshes))] = row
        return list(meshes.values())
