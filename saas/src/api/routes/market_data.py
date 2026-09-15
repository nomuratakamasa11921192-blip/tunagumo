"""reinfolib(不動産情報ライブラリ)のデータを取得し、依頼内容の下書きテキストとして
返すAPI(AI価格査定=5位、空室対策レポート=10位、エリア人口動態分析=11位)。

property_url.pyのextract-urlと同じ設計: セッションは作らず、取得結果を返すだけ。
フロントは結果を「ご依頼内容」入力欄に自動反映し、通常のセッション作成フロー(departments:
valuation/vacancy_report/area_analysis、config/real_estate.yaml参照)に委ねる。

市区町村は、reinfolibの市区町村一覧API(XIT002)から選んでもらう(2026-09-15、それまでは
総務省のコード一覧で5桁コードを調べて手入力する方式で分かりにくかった)。
"""

import statistics
from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.deps import get_current_tenant
from src.core.config import settings
from src.core.models import Tenant
from src.core.reinfolib_client import ReinfolibClient, ReinfolibError

router = APIRouter(prefix="/api/market-data", tags=["market-data"])

NOT_CONFIGURED_DETAIL = "この機能は現在準備中です。お手数ですが運営までお問い合わせください。"

Purpose = Literal["valuation", "vacancy_report", "area_analysis"]

PREFECTURE_NAMES = {
    "01": "北海道", "02": "青森県", "03": "岩手県", "04": "宮城県", "05": "秋田県",
    "06": "山形県", "07": "福島県", "08": "茨城県", "09": "栃木県", "10": "群馬県",
    "11": "埼玉県", "12": "千葉県", "13": "東京都", "14": "神奈川県", "15": "新潟県",
    "16": "富山県", "17": "石川県", "18": "福井県", "19": "山梨県", "20": "長野県",
    "21": "岐阜県", "22": "静岡県", "23": "愛知県", "24": "三重県", "25": "滋賀県",
    "26": "京都府", "27": "大阪府", "28": "兵庫県", "29": "奈良県", "30": "和歌山県",
    "31": "鳥取県", "32": "島根県", "33": "岡山県", "34": "広島県", "35": "山口県",
    "36": "徳島県", "37": "香川県", "38": "愛媛県", "39": "高知県", "40": "福岡県",
    "41": "佐賀県", "42": "長崎県", "43": "熊本県", "44": "大分県", "45": "宮崎県",
    "46": "鹿児島県", "47": "沖縄県",
}

# 要約に載せる件数の上限(依頼文が長くなりすぎないようにする)
MAX_SAMPLE_RECORDS = 15


class MarketDataRequest(BaseModel):
    prefecture_code: str = Field(pattern=r"^\d{2}$")  # 都道府県コード(2桁、例: "11" = 埼玉県)
    city_code: str = Field(pattern=r"^\d{5}$")  # 市区町村コード(5桁、XIT002のid)
    purpose: Purpose


class MarketDataResponse(BaseModel):
    text: str
    notice: str


class Municipality(BaseModel):
    code: str
    name: str


class MunicipalitiesResponse(BaseModel):
    municipalities: list[Municipality]


NOTICE = (
    "取得したデータは国土交通省「不動産情報ライブラリ」の公開情報です。"
    "AIによる要約・分析は参考情報であり、正式な鑑定・保証ではありません。"
)


def _client() -> ReinfolibClient:
    if not settings.reinfolib_api_key:
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED_DETAIL)
    return ReinfolibClient(settings.reinfolib_api_key)


def _to_int(value) -> int | None:
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _man_yen(yen: int) -> str:
    return f"{yen / 10_000:,.0f}万円"


def _summarize_transaction_prices(records: list[dict]) -> str:
    if not records:
        return "対象エリアの取引価格データが見つかりませんでした。"

    prices_by_type: dict[str, list[int]] = defaultdict(list)
    for r in records:
        price = _to_int(r.get("TradePrice"))
        if price:
            prices_by_type[r.get("Type") or "種類不明"].append(price)

    periods = sorted({r["Period"] for r in records if r.get("Period")})
    lines = ["【周辺の不動産価格データ(国土交通省 不動産情報ライブラリ)】"]
    if periods:
        lines.append(f"対象期間: {periods[0]}〜{periods[-1]}(計{len(records)}件)")
    for type_name, prices in sorted(prices_by_type.items(), key=lambda kv: -len(kv[1])):
        lines.append(
            f"- {type_name}: {len(prices)}件、価格の中央値 {_man_yen(int(statistics.median(prices)))}"
            f"(最低 {_man_yen(min(prices))}〜最高 {_man_yen(max(prices))})"
        )

    lines.append("")
    lines.append("【個別の事例(一部)】")
    fields = (
        ("Period", "{}"), ("Type", "{}"), ("DistrictName", "{}"), ("FloorPlan", "間取り{}"),
        ("Area", "面積{}㎡"), ("BuildingYear", "{}築"), ("PriceCategory", "{}"),
    )
    for r in records[:MAX_SAMPLE_RECORDS]:
        parts = [fmt.format(r[key]) for key, fmt in fields if r.get(key)]
        price = _to_int(r.get("TradePrice"))
        if price:
            parts.append(_man_yen(price))
        lines.append("- " + " / ".join(parts))
    return "\n".join(lines)


def _summarize_population_mesh(records: list[dict], area_label: str) -> str:
    if not records:
        return f"{area_label}の将来推計人口データが見つかりませんでした。"

    totals: dict[int, float] = defaultdict(float)
    for r in records:
        for key, value in r.items():
            # PTN_20XX = 20XX年の男女計総数人口(公式マニュアルXKT013)
            if key.startswith("PTN_") and key[4:].isdigit() and isinstance(value, (int, float)):
                totals[int(key[4:])] += value
    if not totals:
        return f"{area_label}の将来推計人口データが見つかりませんでした。"

    years = sorted(totals)
    base = totals[years[0]]
    lines = [
        f"【{area_label}の将来推計人口(国土交通省 不動産情報ライブラリ、250mメッシュ{len(records)}区画の合計)】"
    ]
    for year in years:
        change = f"({(totals[year] / base - 1) * 100:+.1f}%、{years[0]}年比)" if base and year != years[0] else ""
        lines.append(f"- {year}年: 約{totals[year]:,.0f}人{change}")
    lines.append("※ 市区町村の中心から周辺約50km四方の範囲で集計しています。面積の大きい市区町村では一部の区画が含まれない場合があります。")
    return "\n".join(lines)


@router.get("/municipalities", response_model=MunicipalitiesResponse)
async def list_municipalities(
    prefecture_code: str = Query(pattern=r"^\d{2}$"),
    tenant: Tenant = Depends(get_current_tenant),
) -> MunicipalitiesResponse:
    """市区町村の選択肢(XIT002)。フロントの市区町村プルダウンに使う。"""
    client = _client()
    try:
        rows = await client.fetch_municipalities(prefecture_code=prefecture_code)
    except ReinfolibError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return MunicipalitiesResponse(
        municipalities=[Municipality(code=str(r["id"]), name=str(r["name"])) for r in rows if r.get("id") and r.get("name")]
    )


@router.post("/valuation-input", response_model=MarketDataResponse)
async def valuation_input(
    req: MarketDataRequest, tenant: Tenant = Depends(get_current_tenant)
) -> MarketDataResponse:
    return await _fetch(req, kind="valuation")


@router.post("/vacancy-report-input", response_model=MarketDataResponse)
async def vacancy_report_input(
    req: MarketDataRequest, tenant: Tenant = Depends(get_current_tenant)
) -> MarketDataResponse:
    return await _fetch(req, kind="vacancy_report")


@router.post("/area-analysis-input", response_model=MarketDataResponse)
async def area_analysis_input(
    req: MarketDataRequest, tenant: Tenant = Depends(get_current_tenant)
) -> MarketDataResponse:
    return await _fetch(req, kind="area_analysis")


async def _fetch(req: MarketDataRequest, *, kind: Purpose) -> MarketDataResponse:
    prefecture_name = PREFECTURE_NAMES.get(req.prefecture_code)
    if prefecture_name is None or not req.city_code.startswith(req.prefecture_code):
        raise HTTPException(status_code=400, detail="都道府県と市区町村の組み合わせが正しくありません。")

    client = _client()
    try:
        if kind == "area_analysis":
            # 位置の特定には市区町村名が必要なので、一覧APIから正式名称を引く
            municipalities = await client.fetch_municipalities(prefecture_code=req.prefecture_code)
            city_name = next((str(m["name"]) for m in municipalities if str(m.get("id")) == req.city_code), None)
            if city_name is None:
                raise HTTPException(status_code=400, detail="選択された市区町村が見つかりませんでした。")
            area_label = f"{prefecture_name}{city_name}"
            records = await client.fetch_population_mesh(city_code=req.city_code, address=area_label)
            text = _summarize_population_mesh(records, area_label)
        else:
            records = await client.fetch_transaction_prices(city_code=req.city_code)
            text = _summarize_transaction_prices(records)
    except ReinfolibError as e:
        raise HTTPException(status_code=502, detail=str(e))

    purpose_label = {
        "valuation": "この物件の価格査定レポートを作ってください。",
        "vacancy_report": "オーナー様向けの空室対策レポートを作ってください。",
        "area_analysis": "このエリアの人口動態分析レポートを作ってください。",
    }[kind]

    return MarketDataResponse(text=f"{purpose_label}\n\n{text}", notice=NOTICE)
