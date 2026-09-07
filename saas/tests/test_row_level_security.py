"""migrations/versions/0009_row_level_security.py で作ったRLSポリシーが、実際に
DBレベルでテナント分離を強制することを検証する。

このテストはsrc/core/tenant_context.pyのset_tenant_scope()を直接使う低レベルな検証で、
「もしアプリのどこかのクエリでtenant_idのWHERE句を書き忘れても、DB側で止まる」ことの
証明になる。まだどのAPIルートもこの経路を通っていない(admin routesは意図的に
テナント横断で読むため、これを使わずsuperuser接続のままにしてある)。
"""

import uuid

import pytest
from sqlalchemy import select, text

from src.core.db import async_session_factory
from src.core.models import Session as SessionModel
from src.core.tenant_context import set_tenant_scope
from tests.conftest import tenant  # noqa: F401  (fixture)

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _make_other_tenant() -> uuid.UUID:
    """RLS有効下で「他テナント」を作る。superuser接続(既定)なのでRLSの影響を受けない。"""
    other_id = uuid.uuid4()
    async with async_session_factory() as db:
        await db.execute(
            text(
                "INSERT INTO tenants (id, name, api_key_hash, created_at) "
                "VALUES (:id, :name, :hash, now())"
            ),
            {"id": other_id, "name": "rls-other-tenant", "hash": uuid.uuid4().hex},
        )
        await db.commit()
    return other_id


async def _cleanup_tenant(tenant_id: uuid.UUID) -> None:
    async with async_session_factory() as db:
        await db.execute(text("DELETE FROM sessions WHERE tenant_id = :id"), {"id": tenant_id})
        await db.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_id})
        await db.commit()


async def _make_session(tenant_id: uuid.UUID, request_text: str) -> uuid.UUID:
    async with async_session_factory() as db:
        row = SessionModel(tenant_id=tenant_id, request_text=request_text, status="QUEUED")
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.id


async def test_select_only_sees_own_tenants_rows(tenant):
    other_tenant_id = await _make_other_tenant()
    own_session_id = await _make_session(tenant["id"], "自テナントの依頼")
    other_session_id = await _make_session(other_tenant_id, "他テナントの依頼")

    try:
        async with async_session_factory() as db:
            await set_tenant_scope(db, tenant["id"])
            result = await db.execute(select(SessionModel))
            visible_ids = {row.id for row in result.scalars().all()}
            await db.rollback()

        assert own_session_id in visible_ids
        assert other_session_id not in visible_ids
    finally:
        await _cleanup_tenant(other_tenant_id)


async def test_update_cannot_touch_other_tenants_row(tenant):
    other_tenant_id = await _make_other_tenant()
    other_session_id = await _make_session(other_tenant_id, "他テナントの依頼")

    try:
        async with async_session_factory() as db:
            await set_tenant_scope(db, tenant["id"])
            result = await db.execute(
                text("UPDATE sessions SET status = 'HACKED' WHERE id = :id"), {"id": other_session_id}
            )
            affected = result.rowcount
            await db.rollback()

        assert affected == 0

        # superuser接続で見ると、実際に書き換わっていないことを確認する
        async with async_session_factory() as db:
            row = await db.get(SessionModel, other_session_id)
            assert row.status != "HACKED"
    finally:
        await _cleanup_tenant(other_tenant_id)


async def test_delete_cannot_touch_other_tenants_row(tenant):
    other_tenant_id = await _make_other_tenant()
    other_session_id = await _make_session(other_tenant_id, "他テナントの依頼")

    try:
        async with async_session_factory() as db:
            await set_tenant_scope(db, tenant["id"])
            result = await db.execute(text("DELETE FROM sessions WHERE id = :id"), {"id": other_session_id})
            affected = result.rowcount
            await db.rollback()

        assert affected == 0

        async with async_session_factory() as db:
            row = await db.get(SessionModel, other_session_id)
            assert row is not None  # 消えていない
    finally:
        await _cleanup_tenant(other_tenant_id)


async def test_insert_cannot_forge_another_tenants_id(tenant):
    other_tenant_id = await _make_other_tenant()

    try:
        async with async_session_factory() as db:
            await set_tenant_scope(db, tenant["id"])
            with pytest.raises(Exception):
                # 自分のapp.tenant_idと異なるtenant_idでのINSERTは、WITH CHECKで拒否される
                await db.execute(
                    text(
                        "INSERT INTO sessions (id, tenant_id, request_text, status, created_at, updated_at) "
                        "VALUES (:id, :tenant_id, 'なりすまし', 'QUEUED', now(), now())"
                    ),
                    {"id": uuid.uuid4(), "tenant_id": other_tenant_id},
                )
            await db.rollback()
    finally:
        await _cleanup_tenant(other_tenant_id)


async def test_tenant_id_not_set_is_fail_closed(tenant):
    """app.tenant_idをセットし忘れた場合(バグ)、DBが「全部見せる」方向に倒れないことを
    確認する。current_setting('app.tenant_id', true)は未設定だと空文字列を返し、
    それをuuidにキャストしようとしてエラーになる(NULLではなく''になるのはPostgresの
    カスタムGUCの仕様)。これは「何も見せない」よりさらに強い、「エラーで気付ける」
    fail-closedになっている(黙って0件を返すよりも、バグとして目立つ)。"""
    await _make_session(tenant["id"], "見えないはずの依頼")

    async with async_session_factory() as db:
        await db.execute(text("SET LOCAL ROLE app_role"))
        # app.tenant_idはセットしない
        with pytest.raises(Exception):
            await db.execute(select(SessionModel))
        await db.rollback()
