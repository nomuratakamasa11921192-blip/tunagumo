"""物件情報を汎用CSVに書き出すAPI(ポータル一括配信サービス連携用)。
AI呼び出しを伴わない純粋なデータ整形なので、AI予算の消費対象外。
"""

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from src.api.deps import get_current_tenant
from src.core.models import Tenant
from src.core.property_csv_export import CSV_FIELDS, generate_property_csv

router = APIRouter(prefix="/api/property-export", tags=["property-export"])


class PropertyCsvRequest(BaseModel):
    name: str = ""
    deal_type: str = ""  # 賃貸 / 売買
    address: str = ""
    nearest_station: str = ""
    walk_minutes: str = ""
    price: str = ""
    management_fee: str = ""
    deposit: str = ""
    key_money: str = ""
    layout: str = ""
    floor_area: str = ""
    built_year: str = ""
    structure: str = ""
    direction: str = ""
    features: str = ""
    description: str = ""
    image_urls: list[str] = []


_FIELD_MAP = {
    "name": "物件名",
    "deal_type": "取引態様",
    "address": "所在地",
    "nearest_station": "最寄り駅",
    "walk_minutes": "徒歩分",
    "price": "価格・賃料",
    "management_fee": "管理費・共益費",
    "deposit": "敷金",
    "key_money": "礼金",
    "layout": "間取り",
    "floor_area": "専有面積",
    "built_year": "築年数",
    "structure": "構造",
    "direction": "方位",
    "features": "設備・特徴",
    "description": "物件説明",
}


@router.get("/csv-fields")
async def list_csv_fields(tenant: Tenant = Depends(get_current_tenant)) -> dict:
    """出力されるCSVの列名一覧(フロントの説明表示用)。"""
    return {"fields": CSV_FIELDS}


@router.post("/csv")
async def export_property_csv(
    req: PropertyCsvRequest, tenant: Tenant = Depends(get_current_tenant)
) -> Response:
    fields = {jp_key: getattr(req, en_key) for en_key, jp_key in _FIELD_MAP.items()}
    for i, url in enumerate(req.image_urls[:5], start=1):
        fields[f"画像URL{i}"] = url

    csv_bytes = generate_property_csv(fields)
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=property.csv"},
    )
