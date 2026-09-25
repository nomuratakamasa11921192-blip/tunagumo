"""Company allowances, personal access, shared work and legacy compatibility."""
import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from src.core.ai_budget import BudgetExceededError, ensure_budget_available, record_cost, lock_budget_tenant, serialize_company_generation
from src.core.config import settings
from src.core.db import async_session_factory
from src.core.models import Tenant, TenantUser, TenantUserToken, Session, AuditLog
from src.api.deps import hash_api_key

pytestmark = pytest.mark.asyncio(loop_scope="session")


def auth(token):
    return {"Authorization": f"Bearer {token}"}


async def change_plan(client, tenant, plan, **kwargs):
    return await client.put(f"/admin/tenants/{tenant['id']}/plan",
        json={"plan": plan, **kwargs}, headers=auth(settings.admin_api_key))


@pytest.mark.parametrize("plan,cap", [("basic", 30), ("business", 60)])
async def test_new_allowances_warning_stop_and_month_reset(client, tenant, plan, cap):
    assert (await change_plan(client, tenant, plan)).status_code == 200
    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        row.ai_cost_this_period_usd = cap * .8
        await db.commit()
    u = (await client.get('/api/account/usage', headers=auth(tenant['api_key']))).json()
    assert u['usage_state'] == 'warning' and u['usage_scope'] == 'company'
    assert u['ai_usage_percent'] == 80 and u['self_service_addon'] is False
    assert datetime.fromisoformat(u['resets_at'].replace('Z', '+00:00')).day == 1
    assert not any('usd' in k or 'cost' in k or 'budget' in k for k in u)
    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        row.ai_cost_this_period_usd = cap
        await db.commit()
    stopped = await client.post('/api/sessions', headers=auth(tenant['api_key']), json={'text':'test'})
    assert stopped.status_code == 429
    assert (await client.get('/api/sessions', headers=auth(tenant['api_key']))).status_code == 200
    assert (await client.get('/api/documents', headers=auth(tenant['api_key']))).status_code == 200
    assert (await client.post('/api/account/buy-addon', headers=auth(tenant['api_key']))).status_code == 409
    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant["id"])
        row.ai_cost_period_started_at = datetime.utcnow() - timedelta(days=45)
        ensure_budget_available(row)
        assert row.ai_cost_this_period_usd == 0


async def test_enterprise_needs_finite_contract_allowance(client, tenant):
    assert (await change_plan(client, tenant, 'enterprise')).status_code == 400
    assert (await change_plan(client, tenant, 'enterprise', enterprise_ai_budget_usd=-1)).status_code == 422
    res = await change_plan(client, tenant, 'enterprise', enterprise_ai_budget_usd=125)
    assert res.status_code == 200 and res.json()['monthly_ai_budget_usd'] == 125
    assert (await change_plan(client, tenant, 'business', enterprise_ai_budget_usd=125)).status_code == 400
    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant['id'])
        row.enterprise_ai_budget_usd = None
        row.addon_credit_usd = 10
        with pytest.raises(BudgetExceededError):
            ensure_budget_available(row)


async def test_legacy_plan_is_unchanged_and_new_cost_does_not_disappear(client, tenant):
    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant['id'])
        assert row.plan == 'light'
        row.ai_cost_this_period_usd = 10
        with pytest.raises(BudgetExceededError):
            ensure_budget_available(row)
        row.plan = 'basic'
        row.ai_cost_this_period_usd = 29.9
        record_cost(row, .5)
        assert row.ai_cost_this_period_usd == pytest.approx(30.4)


async def test_parallel_workers_cannot_lose_company_cost(client, tenant):
    await change_plan(client, tenant, 'basic')
    async def spend():
        async with async_session_factory() as db:
            row = await lock_budget_tenant(db, tenant['id'])
            ensure_budget_available(row)
            await asyncio.sleep(.02)
            record_cost(row, 1)
            await db.commit()
    await asyncio.gather(spend(), spend(), spend())
    async with async_session_factory() as db:
        assert (await db.get(Tenant, tenant['id'])).ai_cost_this_period_usd == 3


async def test_background_month_rollover_saves_cost_without_deadlock(tenant, config):
    async with async_session_factory() as db:
        row = await db.get(Tenant, tenant['id'])
        row.plan = 'basic'
        row.ai_cost_this_period_usd = 30
        row.ai_cost_period_started_at = datetime.utcnow() - timedelta(days=45)
        session = Session(tenant_id=row.id, status='QUEUED', request_text='月替わりの依頼')
        db.add(session)
        await db.commit()
        sid = str(session.id)
    embedding = SimpleNamespace(total_cost_usd=0.0)

    @serialize_company_generation
    async def run_new_session(**kwargs):
        from src.agent.runner import _persist
        embedding.total_cost_usd += .1
        await _persist(str(tenant['id']), sid, 'COMPLETED', {'cost_usd': .2},
                       app_config=kwargs['app_config'])

    await asyncio.wait_for(run_new_session(tenant_id=str(tenant['id']), session_id=sid,
                            app_config=config, embedding_provider=embedding), timeout=5)
    async with async_session_factory() as db:
        assert (await db.get(Tenant, tenant['id'])).ai_cost_this_period_usd == pytest.approx(.3)
        assert (await db.get(Session, uuid.UUID(sid))).status == 'COMPLETED'


async def invite_and_login(client, tenant, monkeypatch, email, actor=None, role='member'):
    captured = {}
    async def email_fake(**kw):
        captured.update(kw)
        return True
    monkeypatch.setattr('src.api.routes.account.send_email', email_fake)
    res = await client.post('/api/account/users', headers=auth(actor or tenant['api_key']),
                            json={'email':email, 'role':role})
    assert res.status_code == 201, res.text
    token = re.search(r'招待コード: <code>([^<]+)</code>', captured['html'])[1]
    accepted = await client.post('/api/account/accept-invite', json={'invite_token':token,'password':'test company password'})
    assert accepted.status_code == 200
    res = await client.post('/api/account/member-login', json={'company_id':str(tenant['id']),
                            'email':email,'password':'test company password'})
    assert res.status_code == 200, res.text
    return res.json()


async def test_individual_login_shared_allowance_roles_and_revocation(client, tenant, monkeypatch):
    await change_plan(client, tenant, 'basic')
    owner = await invite_and_login(client, tenant, monkeypatch, 'owner@example.com')
    member = await invite_and_login(client, tenant, monkeypatch, 'member@example.com', owner['token'])
    for user in [owner, member]:
        usage = await client.get('/api/account/usage', headers=auth(user['token']))
        assert usage.status_code == 200 and usage.json()['plan'] == 'basic'
    assert (await client.post('/api/account/billing-portal', headers=auth(member['token']))).status_code == 403
    assert (await client.put('/api/account/inquiry-settings', headers=auth(member['token']),json={})).status_code == 403
    assert (await client.post('/api/account/users', headers=auth(member['token']),json={'email':'x@example.com'})).status_code == 403
    missing = str(uuid.uuid4())
    assert (await client.post(f'/api/proposals/{missing}/send', headers=auth(member['token']))).status_code == 403
    assert (await client.post(f'/api/inquiries/{missing}/reply', headers=auth(member['token']),json={'text':'test'})).status_code == 403
    assert (await client.put('/api/proposal-settings', headers=auth(member['token']),json={})).status_code == 403
    duplicate = await client.post('/api/account/users', headers=auth(owner['token']),json={'email':'MEMBER@example.com'})
    assert duplicate.status_code == 409
    assert (await client.delete('/api/account/users/'+owner['tenant_user_id'],headers=auth(owner['token']))).status_code == 409
    assert (await client.delete('/api/account/users/'+member['tenant_user_id'],headers=auth(owner['token']))).status_code == 204
    assert (await client.get('/api/sessions',headers=auth(member['token']))).status_code == 401
    assert (await client.post('/api/account/logout',headers=auth(owner['token']))).status_code == 204
    assert (await client.get('/api/sessions',headers=auth(owner['token']))).status_code == 401


async def test_shared_work_and_notes_stay_within_company(client, tenant, monkeypatch):
    owner = await invite_and_login(client, tenant, monkeypatch, 'owner@example.com')
    member = await invite_and_login(client, tenant, monkeypatch, 'member@example.com', owner['token'])
    # 生成は別の統合テストで検証。ここでは承認待ちの共有成果物を用意する。
    async with async_session_factory() as db:
        session = Session(tenant_id=tenant['id'], status='COMPLETED', request_text='物件紹介文', result={'reply':'完成した文章'})
        db.add(session); await db.flush()
        db.add(AuditLog(tenant_id=tenant['id'],session_id=session.id,action='REQUEST_CREATED',actor=member['tenant_user_id']))
        await db.commit(); sid = str(session.id)
    assert (await client.post(f'/api/sessions/{sid}/comments', headers=auth(member['token']),json={'comment':'確認をお願いします'})).status_code == 201
    feed = (await client.get('/api/account/activity',headers=auth(owner['token']))).json()
    assert feed['requests'][0]['requester'] == 'member@example.com'
    assert feed['events'][0]['comment'] == '確認をお願いします'
    assert (await client.get(f'/api/sessions/{sid}',headers=auth(owner['token']))).json()['result']['reply'] == '完成した文章'
    async with async_session_factory() as db:
        other = Tenant(name='別会社',api_key_hash=hash_api_key('other-company-key'))
        db.add(other); await db.commit(); other_id = other.id
    try:
        response = await client.get('/api/account/activity',headers=auth('other-company-key'))
        assert response.json() == {'requests':[], 'events':[]}
        assert (await client.post(f'/api/sessions/{sid}/comments',headers=auth('other-company-key'),json={'comment':'侵入'})).status_code == 404
        assert (await client.get(f'/api/sessions/{sid}',headers=auth('other-company-key'))).status_code == 404
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(Tenant).where(Tenant.id == other_id)); await db.commit()


async def test_personal_login_expires_and_locks_after_repeated_failure(client, tenant, monkeypatch):
    user = await invite_and_login(client, tenant, monkeypatch, 'owner@example.com')
    async with async_session_factory() as db:
        token = (await db.execute(select(TenantUserToken).where(TenantUserToken.token_hash == hash_api_key(user['token'])))).scalar_one()
        token.expires_at = datetime.utcnow() - timedelta(seconds=1); await db.commit()
    assert (await client.get('/api/sessions',headers=auth(user['token']))).status_code == 401
    credentials = {'company_id':str(tenant['id']),'email':'owner@example.com','password':'wrong'}
    for _ in range(5):
        assert (await client.post('/api/account/member-login', json=credentials)).status_code == 401
    credentials['password'] = 'test company password'
    assert (await client.post('/api/account/member-login', json=credentials)).status_code == 401
