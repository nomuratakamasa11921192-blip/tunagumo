import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _invite(client, tenant, email, role=None, actor_token=None):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    if actor_token:
        headers["X-User-Token"] = actor_token
    body = {"email": email}
    if role:
        body["role"] = role
    return await client.post("/api/account/users", json=body, headers=headers)


async def test_first_invite_becomes_owner_without_any_existing_user(client, tenant):
    res = await _invite(client, tenant, "owner@example.com", role="member")
    assert res.status_code == 201
    body = res.json()
    # 最初の1人は誰も居ない状態から招待されるので、指定したroleに関わらずownerになる
    assert body["role"] == "owner"


async def test_second_invite_without_owner_actor_is_forbidden(client, tenant):
    first = await _invite(client, tenant, "owner1@example.com")
    assert first.status_code == 201

    second = await _invite(client, tenant, "member1@example.com")
    assert second.status_code == 403


async def test_full_invite_login_flow(client, tenant, monkeypatch):
    captured = {}

    async def fake_send_email(*, to, subject, html):
        captured["html"] = html
        return True

    monkeypatch.setattr("src.api.routes.account.send_email", fake_send_email)

    invited = await _invite(client, tenant, "owner@example.com")
    assert invited.status_code == 201

    # メール本文に埋め込まれた招待コードを取り出す(実際のメール受信の代わり)
    import re

    match = re.search(r"招待コード: <code>([^<]+)</code>", captured["html"])
    assert match is not None
    raw_invite_token = match.group(1)

    accepted = await client.post(
        "/api/account/accept-invite",
        json={"invite_token": raw_invite_token, "password": "correct horse battery"},
    )
    assert accepted.status_code == 200

    # 期限切れ・使用済みの招待コードは再利用できない
    reused = await client.post(
        "/api/account/accept-invite",
        json={"invite_token": raw_invite_token, "password": "another password here"},
    )
    assert reused.status_code == 400

    login = await client.post(
        "/api/account/login",
        json={"email": "owner@example.com", "password": "correct horse battery"},
        headers={"Authorization": f"Bearer {tenant['api_key']}"},
    )
    assert login.status_code == 200
    login_body = login.json()
    assert login_body["role"] == "owner"
    assert login_body["token"]

    wrong = await client.post(
        "/api/account/login",
        json={"email": "owner@example.com", "password": "wrong password"},
        headers={"Authorization": f"Bearer {tenant['api_key']}"},
    )
    assert wrong.status_code == 401

    users = await client.get(
        "/api/account/users",
        headers={
            "Authorization": f"Bearer {tenant['api_key']}",
            "X-User-Token": login_body["token"],
        },
    )
    assert users.status_code == 200
    assert len(users.json()["users"]) == 1
    assert users.json()["users"][0]["email"] == "owner@example.com"


async def test_list_users_requires_owner_role(client, tenant, monkeypatch):
    captured = {}

    async def fake_send_email(*, to, subject, html):
        captured["html"] = html
        return True

    monkeypatch.setattr("src.api.routes.account.send_email", fake_send_email)

    await _invite(client, tenant, "owner@example.com")

    import re

    owner_token_match = re.search(r"招待コード: <code>([^<]+)</code>", captured["html"])
    await client.post(
        "/api/account/accept-invite",
        json={"invite_token": owner_token_match.group(1), "password": "owner password 1"},
    )
    owner_login = await client.post(
        "/api/account/login",
        json={"email": "owner@example.com", "password": "owner password 1"},
        headers={"Authorization": f"Bearer {tenant['api_key']}"},
    )
    owner_token = owner_login.json()["token"]

    invited_member = await _invite(
        client, tenant, "member@example.com", role="member", actor_token=owner_token
    )
    assert invited_member.status_code == 201

    member_token_match = re.search(r"招待コード: <code>([^<]+)</code>", captured["html"])
    await client.post(
        "/api/account/accept-invite",
        json={"invite_token": member_token_match.group(1), "password": "member password 1"},
    )
    member_login = await client.post(
        "/api/account/login",
        json={"email": "member@example.com", "password": "member password 1"},
        headers={"Authorization": f"Bearer {tenant['api_key']}"},
    )
    member_token = member_login.json()["token"]

    forbidden = await client.get(
        "/api/account/users",
        headers={
            "Authorization": f"Bearer {tenant['api_key']}",
            "X-User-Token": member_token,
        },
    )
    assert forbidden.status_code == 403


async def test_invalid_user_token_is_rejected(client, tenant):
    res = await client.get(
        "/api/account/users",
        headers={
            "Authorization": f"Bearer {tenant['api_key']}",
            "X-User-Token": "not-a-real-token",
        },
    )
    assert res.status_code == 401
