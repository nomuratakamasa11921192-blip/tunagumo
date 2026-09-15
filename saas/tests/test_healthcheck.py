import pytest

from src.core.healthcheck import check_openai, check_db

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_check_db_succeeds_against_real_test_database():
    result = await check_db()
    assert result.ok is True


async def test_check_openai_succeeds_with_working_client():
    class _OkClient:
        class _Models:
            async def retrieve(self, model):
                return object()

        models = _Models()

    result = await check_openai(_OkClient())
    assert result.ok is True


async def test_check_openai_fails_with_broken_client():
    class _BrokenClient:
        class _Models:
            async def retrieve(self, model):
                raise RuntimeError("APIキーが無効です")

        models = _Models()

    result = await check_openai(_BrokenClient())
    assert result.ok is False
    assert "APIキーが無効です" in result.detail
