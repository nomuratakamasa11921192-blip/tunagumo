"""reinfolib(不動産情報ライブラリ)のデータを取得し、依頼内容の下書きテキストとして
返すAPI(AI価格査定=5位、空室対策レポート=10位、エリア人口動態分析=11位)。

property_url.pyのextract-urlと同じ設計: セッションは作らず、取得結果を返すだけ。
フロントは結果を「ゴール」入力欄に自動反映し、通常のセッション作成フロー(departments:
valuation/vacancy_report/area_analysis、config/real_estate.yaml参照)に委ねる。

市区町村コードは顧客に直接入力してもらう(総務省の全国地方公共団体コード一覧を案内する)。
正確性が重要な政府コードを当てずっぽうで内蔵データ化することはしない。
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.deps import get_current_tenant
from src.core.config import settings
from src.core.models import Tenant
from src.core.reinfolib_client import ReinfolibClient, ReinfolibError

router = APIRouter(prefix="/api/market-data", tags=["market-data"])

NOT_CONFIGURED_DETAIL = (
    "本機能は準備中です(reinfolib APIキーが未設定のため)。運営にお問い合わせください。"
)

Purpose = Literal["valuation", "vacancy_report", "area_analysis"]


class MarketDataRequest(BaseModel):
    prefecture_code: str  # 都道府県コード(2桁、例: "11" = 埼玉県)
    city_code: str  # 市区町村コード(5桁)。総務省の全国地方公共団体コード一覧で確認
    purpose: Purpose


class MarketDataResponse(BaseModel):
    text: str
    notice: str


NOTICE = (
    "取得したデータは国土交通省「不動産情報ライブラリ」の公開情報です。"
    "AIによる要約・分析は参考情報であり、正式な鑑定・保証ではありません。"
)


def _summarize_transaction_prices(records: list[dict]) -> str:
    if not records:
        return "対象エリアの取引価格データが見つかりませんでした。"
    lines = ["【周辺の取引価格データ(reinfolib、直近分)】"]
    for r in records[:20]:
        lines.append(
            "- "
            + " / ".join(
                f"{k}: {v}"
                for k, v in r.items()
                if k in ("Type", "Price", "Area", "Layout", "Period", "Municipality", "DistrictName")
                and v
            )
        )
    return "\n".join(lines)


def _summarize_population_mesh(records: list[dict]) -> str:
    if not records:
        return "対象エリアの人口統計データが見つかりませんでした。"
    lines = ["【エリアの人口統計データ(reinfolib)】"]
    for r in records[:20]:
        lines.append("- " + " / ".join(f"{k}: {v}" for k, v in r.items() if v))
    return "\n".join(lines)


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
    if not settings.reinfolib_api_key:
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED_DETAIL)

    client = ReinfolibClient(settings.reinfolib_api_key)
    try:
        if kind == "area_analysis":
            records = await client.fetch_population_mesh(city_code=req.city_code)
            text = _summarize_population_mesh(records)
        else:
            records = await client.fetch_transaction_prices(
                prefecture_code=req.prefecture_code, city_code=req.city_code
            )
            text = _summarize_transaction_prices(records)
    except ReinfolibError as e:
        raise HTTPException(status_code=502, detail=str(e))

    purpose_label = {
        "valuation": "この物件の価格査定レポートを作ってください。",
        "vacancy_report": "オーナー様向けの空室対策レポートを作ってください。",
        "area_analysis": "このエリアの人口動態分析レポートを作ってください。",
    }[kind]

    return MarketDataResponse(text=f"{purpose_label}\n\n{text}", notice=NOTICE)
