import pytest

from src.core.healthcheck import check_anthropic, check_db

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_check_db_succeeds_against_real_test_database():
    result = await check_db()
    assert result.ok is True


async def test_check_anthropic_succeeds_with_working_client():
    class _OkClient:
        class _Messages:
            async def create(self, **kwargs):
                return object()

        messages = _Messages()

    result = await check_anthropic(_OkClient())
    assert result.ok is True


async def test_check_anthropic_fails_with_broken_client():
    class _BrokenClient:
        class _Messages:
            async def create(self, **kwargs):
                raise RuntimeError("APIキーが無効です")

        messages = _Messages()

    result = await check_anthropic(_BrokenClient())
    assert result.ok is False
    assert "APIキーが無効です" in result.detail
