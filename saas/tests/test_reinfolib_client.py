from datetime import date

import httpx
import pytest

from src.core.reinfolib_client import (
    GSI_ADDRESS_SEARCH_URL,
    ReinfolibClient,
    ReinfolibError,
    lonlat_to_tile,
    previous_quarters,
)

_async = pytest.mark.asyncio(loop_scope="session")


def _client(handler) -> ReinfolibClient:
    return ReinfolibClient("test-key", transport=httpx.MockTransport(handler))


def test_previous_quarters_wraps_year():
    assert previous_quarters(date(2026, 2, 10), 3) == [(2025, 4), (2025, 3), (2025, 2)]
    assert previous_quarters(date(2026, 9, 15), 2) == [(2026, 2), (2026, 1)]


def test_lonlat_to_tile_matches_known_value():
    # 東京駅(139.7671, 35.6812)はz=11でx=1819, y=806
    assert lonlat_to_tile(139.7671, 35.6812, 11) == (1819, 806)


@_async
async def test_fetch_transaction_prices_sends_required_params_and_collects_quarters():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Ocp-Apim-Subscription-Key"] == "test-key"
        assert request.url.path.endswith("/XIT001")
        params = dict(request.url.params)
        calls.append((params["year"], params["quarter"]))
        assert params["city"] == "11214"
        # 直近の四半期(2026Q2)はまだ未公開という想定
        if (params["year"], params["quarter"]) == ("2026", "2"):
            return httpx.Response(404)
        return httpx.Response(200, json={"status": "OK", "data": [{"TradePrice": "10000000", "Period": params["year"]}]})

    records = await _client(handler).fetch_transaction_prices(city_code="11214", today=date(2026, 9, 15))

    assert calls == [("2026", "2"), ("2026", "1"), ("2025", "4"), ("2025", "3"), ("2025", "2")]
    assert len(records) == 4


@_async
async def test_fetch_transaction_prices_raises_on_auth_error():
    client = _client(lambda req: httpx.Response(401, text="invalid subscription key"))
    with pytest.raises(ReinfolibError):
        await client.fetch_transaction_prices(city_code="11214", today=date(2026, 9, 15))


@_async
async def test_fetch_raises_on_unparseable_response():
    client = _client(lambda req: httpx.Response(200, text="not json"))
    with pytest.raises(ReinfolibError):
        await client.fetch_municipalities(prefecture_code="11")


@_async
async def test_fetch_municipalities():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/XIT002")
        assert request.url.params["area"] == "11"
        return httpx.Response(200, json={"status": "OK", "data": [{"id": "11214", "name": "春日部市"}]})

    assert await _client(handler).fetch_municipalities(prefecture_code="11") == [{"id": "11214", "name": "春日部市"}]


@_async
async def test_fetch_population_mesh_geocodes_then_filters_by_city():
    tile_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(GSI_ADDRESS_SEARCH_URL):
            assert "Ocp-Apim-Subscription-Key" not in request.headers  # 国土地理院にキーを送らない
            assert request.url.params["q"] == "埼玉県春日部市"
            return httpx.Response(200, json=[{"geometry": {"coordinates": [139.7547, 35.9740]}}])
        assert request.url.path.endswith("/XKT013")
        params = dict(request.url.params)
        assert params["response_format"] == "geojson" and params["z"] == "11"
        tile_requests.append((params["x"], params["y"]))
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    {"properties": {"MESH_ID": "A", "SHICODE": "11214", "PTN_2020": 100}},
                    {"properties": {"MESH_ID": "B", "SHICODE": "11238", "PTN_2020": 999}},
                ],
            },
        )

    records = await _client(handler).fetch_population_mesh(city_code="11214", address="埼玉県春日部市")

    assert len(tile_requests) == 9
    assert records == [{"MESH_ID": "A", "SHICODE": "11214", "PTN_2020": 100}]  # 他市と重複を除外


@_async
async def test_fetch_population_mesh_raises_when_location_not_found():
    client = _client(lambda req: httpx.Response(200, json=[]))
    with pytest.raises(ReinfolibError):
        await client.fetch_population_mesh(city_code="11214", address="存在しない市")


def test_client_rejects_empty_api_key():
    with pytest.raises(ReinfolibError):
        ReinfolibClient("")
