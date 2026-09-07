import pytest

from src.core.config import settings

pytestmark = pytest.mark.asyncio(loop_scope="session")

REQUEST_BODY = {"prefecture_code": "11", "city_code": "11213", "purpose": "valuation"}


async def test_valuation_input_requires_auth(client):
    res = await client.post("/api/market-data/valuation-input", json=REQUEST_BODY)
    assert res.status_code == 401


async def test_valuation_input_returns_503_when_not_configured(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "reinfolib_api_key", "")
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post("/api/market-data/valuation-input", json=REQUEST_BODY, headers=headers)
    assert res.status_code == 503


async def test_valuation_input_returns_summarized_text(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "reinfolib_api_key", "test-key")

    async def fake_fetch_transaction_prices(self, *, prefecture_code, city_code, property_type=None, year=None):
        return [{"Type": "宅地", "Price": "8000000", "Area": "60"}]

    monkeypatch.setattr(
        "src.core.reinfolib_client.ReinfolibClient.fetch_transaction_prices",
        fake_fetch_transaction_prices,
    )

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post("/api/market-data/valuation-input", json=REQUEST_BODY, headers=headers)

    assert res.status_code == 200
    body = res.json()
    assert "8000000" in body["text"]
    assert "参考情報" in body["notice"]


async def test_vacancy_report_input_uses_transaction_prices(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "reinfolib_api_key", "test-key")
    calls = []

    async def fake_fetch_transaction_prices(self, *, prefecture_code, city_code, property_type=None, year=None):
        calls.append((prefecture_code, city_code))
        return []

    monkeypatch.setattr(
        "src.core.reinfolib_client.ReinfolibClient.fetch_transaction_prices",
        fake_fetch_transaction_prices,
    )

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post(
        "/api/market-data/vacancy-report-input",
        json={"prefecture_code": "11", "city_code": "11213", "purpose": "vacancy_report"},
        headers=headers,
    )

    assert res.status_code == 200
    assert calls == [("11", "11213")]


async def test_area_analysis_input_uses_population_mesh(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "reinfolib_api_key", "test-key")

    async def fake_fetch_population_mesh(self, *, city_code):
        return [{"population": "12345"}]

    monkeypatch.setattr(
        "src.core.reinfolib_client.ReinfolibClient.fetch_population_mesh",
        fake_fetch_population_mesh,
    )

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post(
        "/api/market-data/area-analysis-input",
        json={"prefecture_code": "11", "city_code": "11213", "purpose": "area_analysis"},
        headers=headers,
    )

    assert res.status_code == 200
    assert "12345" in res.json()["text"]


async def test_valuation_input_returns_502_on_reinfolib_error(client, tenant, monkeypatch):
    monkeypatch.setattr(settings, "reinfolib_api_key", "test-key")

    async def fake_fetch_transaction_prices(self, *, prefecture_code, city_code, property_type=None, year=None):
        from src.core.reinfolib_client import ReinfolibError

        raise ReinfolibError("reinfolib APIエラー")

    monkeypatch.setattr(
        "src.core.reinfolib_client.ReinfolibClient.fetch_transaction_prices",
        fake_fetch_transaction_prices,
    )

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post("/api/market-data/valuation-input", json=REQUEST_BODY, headers=headers)
    assert res.status_code == 502
