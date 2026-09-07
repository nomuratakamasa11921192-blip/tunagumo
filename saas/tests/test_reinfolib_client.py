import httpx
import pytest

from src.core.reinfolib_client import ReinfolibClient, ReinfolibError

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_fetch_transaction_prices_sends_expected_params_and_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Ocp-Apim-Subscription-Key"] == "test-key"
        assert request.url.path.endswith("/XIT001")
        assert request.url.params["area"] == "11"
        assert request.url.params["city"] == "11213"
        return httpx.Response(200, json={"data": [{"Type": "宅地(土地と建物)", "Price": "10000000"}]})

    client = ReinfolibClient("test-key", transport=_transport(handler))
    records = await client.fetch_transaction_prices(prefecture_code="11", city_code="11213")

    assert records == [{"Type": "宅地(土地と建物)", "Price": "10000000"}]


async def test_fetch_transaction_prices_handles_plain_list_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"Price": "5000000"}])

    client = ReinfolibClient("test-key", transport=_transport(handler))
    records = await client.fetch_transaction_prices(prefecture_code="11", city_code="11213")

    assert records == [{"Price": "5000000"}]


async def test_fetch_raises_on_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid subscription key")

    client = ReinfolibClient("bad-key", transport=_transport(handler))
    with pytest.raises(ReinfolibError):
        await client.fetch_transaction_prices(prefecture_code="11", city_code="11213")


async def test_fetch_raises_on_unparseable_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    client = ReinfolibClient("test-key", transport=_transport(handler))
    with pytest.raises(ReinfolibError):
        await client.fetch_transaction_prices(prefecture_code="11", city_code="11213")


async def test_fetch_population_mesh_sends_expected_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/XKT013")
        assert request.url.params["city"] == "11213"
        return httpx.Response(200, json={"data": [{"population": "1000"}]})

    client = ReinfolibClient("test-key", transport=_transport(handler))
    records = await client.fetch_population_mesh(city_code="11213")

    assert records == [{"population": "1000"}]


def test_client_rejects_empty_api_key():
    with pytest.raises(ReinfolibError):
        ReinfolibClient("")
