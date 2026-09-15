import pytest

from src.core.config import settings
from src.core.reinfolib_client import ReinfolibClient, ReinfolibError

pytestmark = pytest.mark.asyncio(loop_scope="session")

REQUEST_BODY = {"prefecture_code": "11", "city_code": "11214", "purpose": "valuation"}


def _headers(tenant):
    return {"Authorization": f"Bearer {tenant['api_key']}"}


async def test_valuation_input_requires_auth(client):
    res = await client.post("/api/market-data/valuation-input", json=REQUEST_BODY)
    assert res.status_code == 401


async def test_valuation_input_returns_503_when_not_configured(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "reinfolib_api_key", "")
    res = await client.post("/api/market-data/valuation-input", json=REQUEST_BODY, headers=_headers(tenant))
    assert res.status_code == 503


async def test_rejects_mismatched_prefecture_and_city(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "reinfolib_api_key", "test-key")
    body = {**REQUEST_BODY, "city_code": "13101"}
    res = await client.post("/api/market-data/valuation-input", json=body, headers=_headers(tenant))
    assert res.status_code == 400


async def test_rejects_malformed_city_code(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "reinfolib_api_key", "test-key")
    body = {**REQUEST_BODY, "city_code": "1121"}
    res = await client.post("/api/market-data/valuation-input", json=body, headers=_headers(tenant))
    assert res.status_code == 422


async def test_valuation_input_summarizes_with_median_by_type(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "reinfolib_api_key", "test-key")

    async def fake(self, *, city_code, price_classification=None, today=None):
        return [
            {"Type": "中古マンション等", "TradePrice": "20000000", "FloorPlan": "３ＬＤＫ", "Area": "70", "Period": "2025年第4四半期"},
            {"Type": "中古マンション等", "TradePrice": "30000000", "Period": "2026年第1四半期"},
            {"Type": "中古マンション等", "TradePrice": "25000000", "Period": "2026年第1四半期"},
        ]

    monkeypatch.setattr(ReinfolibClient, "fetch_transaction_prices", fake)
    res = await client.post("/api/market-data/valuation-input", json=REQUEST_BODY, headers=_headers(tenant))

    assert res.status_code == 200
    text = res.json()["text"]
    assert "中古マンション等: 3件、価格の中央値 2,500万円" in text
    assert "間取り３ＬＤＫ" in text
    assert "参考情報" in res.json()["notice"]


async def test_vacancy_report_input_uses_transaction_prices(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "reinfolib_api_key", "test-key")
    calls = []

    async def fake(self, *, city_code, price_classification=None, today=None):
        calls.append(city_code)
        return []

    monkeypatch.setattr(ReinfolibClient, "fetch_transaction_prices", fake)
    body = {**REQUEST_BODY, "purpose": "vacancy_report"}
    res = await client.post("/api/market-data/vacancy-report-input", json=body, headers=_headers(tenant))

    assert res.status_code == 200
    assert calls == ["11214"]
    assert "見つかりませんでした" in res.json()["text"]


async def test_area_analysis_input_aggregates_population(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "reinfolib_api_key", "test-key")
    seen = {}

    async def fake_municipalities(self, *, prefecture_code):
        return [{"id": "11214", "name": "春日部市"}]

    async def fake_mesh(self, *, city_code, address):
        seen["address"] = address
        return [{"PTN_2020": 1000, "PTN_2050": 800}, {"PTN_2020": 1000, "PTN_2050": 700}]

    monkeypatch.setattr(ReinfolibClient, "fetch_municipalities", fake_municipalities)
    monkeypatch.setattr(ReinfolibClient, "fetch_population_mesh", fake_mesh)
    body = {**REQUEST_BODY, "purpose": "area_analysis"}
    res = await client.post("/api/market-data/area-analysis-input", json=body, headers=_headers(tenant))

    assert res.status_code == 200
    assert seen["address"] == "埼玉県春日部市"
    text = res.json()["text"]
    assert "2020年: 約2,000人" in text
    assert "2050年: 約1,500人(-25.0%、2020年比)" in text


async def test_municipalities_endpoint(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "reinfolib_api_key", "test-key")

    async def fake(self, *, prefecture_code):
        return [{"id": "11214", "name": "春日部市"}]

    monkeypatch.setattr(ReinfolibClient, "fetch_municipalities", fake)
    res = await client.get("/api/market-data/municipalities?prefecture_code=11", headers=_headers(tenant))
    assert res.status_code == 200
    assert res.json() == {"municipalities": [{"code": "11214", "name": "春日部市"}]}


async def test_valuation_input_returns_502_on_reinfolib_error(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "reinfolib_api_key", "test-key")

    async def fake(self, *, city_code, price_classification=None, today=None):
        raise ReinfolibError("取得できませんでした")

    monkeypatch.setattr(ReinfolibClient, "fetch_transaction_prices", fake)
    res = await client.post("/api/market-data/valuation-input", json=REQUEST_BODY, headers=_headers(tenant))
    assert res.status_code == 502
