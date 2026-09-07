import pytest

from src.api.deps import get_tts_provider
from src.main import app

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _FakeTTSProvider:
    async def synthesize(self, text, *, voice="alloy"):
        return b"fake-mp3-bytes"


@pytest.fixture
def with_fake_tts_provider():
    app.dependency_overrides[get_tts_provider] = lambda: _FakeTTSProvider()
    yield
    app.dependency_overrides.pop(get_tts_provider, None)


def _photo_files():
    return [("photos", ("a.jpg", b"fake-jpeg-bytes", "image/jpeg"))]


def _video_files():
    return [("video", ("a.mp4", b"fake-mp4-bytes", "video/mp4"))]


async def test_generate_room_tour_requires_auth(client):
    res = await client.post(
        "/api/room-tour/generate", data={"property_info": "テスト物件"}, files=_photo_files()
    )
    assert res.status_code == 401


async def test_generate_room_tour_without_openai_key_returns_402(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post(
        "/api/room-tour/generate",
        data={"property_info": "テスト物件"},
        files=_photo_files(),
        headers=headers,
    )
    assert res.status_code == 402


async def test_generate_room_tour_rejects_empty_property_info(client, tenant, with_fake_tts_provider):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post(
        "/api/room-tour/generate", data={"property_info": "  "}, files=_photo_files(), headers=headers
    )
    assert res.status_code == 400


async def test_generate_room_tour_rejects_when_no_media_given(client, tenant, with_fake_tts_provider):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post(
        "/api/room-tour/generate", data={"property_info": "テスト物件"}, headers=headers
    )
    assert res.status_code == 400


async def test_generate_room_tour_rejects_unsupported_photo_content_type(client, tenant, with_fake_tts_provider):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    files = [("photos", ("a.gif", b"fake-gif-bytes", "image/gif"))]
    res = await client.post(
        "/api/room-tour/generate", data={"property_info": "テスト物件"}, files=files, headers=headers
    )
    assert res.status_code == 400


async def test_generate_room_tour_rejects_unsupported_video_content_type(client, tenant, with_fake_tts_provider):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    files = [("video", ("a.avi", b"fake-avi-bytes", "video/x-msvideo"))]
    res = await client.post(
        "/api/room-tour/generate", data={"property_info": "テスト物件"}, files=files, headers=headers
    )
    assert res.status_code == 400


async def test_generate_room_tour_rejects_unknown_bgm(client, tenant, with_fake_tts_provider):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post(
        "/api/room-tour/generate",
        data={"property_info": "テスト物件", "bgm": "does-not-exist"},
        files=_photo_files(),
        headers=headers,
    )
    assert res.status_code == 400


async def test_generate_room_tour_calls_pipeline_with_photos_and_returns_video(
    client, tenant, with_fake_tts_provider, monkeypatch
):
    from pathlib import Path

    from src.video.paths import WORKSPACE_ROOT

    captured = {}

    async def fake_generate_room_tour(
        *, llm, model, property_info, image_paths, video_path=None, tts_provider, bgm_path=None, job_dir=None
    ):
        captured["image_paths"] = image_paths
        captured["video_path"] = video_path
        out = WORKSPACE_ROOT / job_dir / "final.mp4"
        out.write_bytes(b"fake-final-mp4-bytes")
        return out

    monkeypatch.setattr("src.api.routes.room_tour.generate_room_tour", fake_generate_room_tour)

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post(
        "/api/room-tour/generate",
        data={"property_info": "テスト物件、2LDK"},
        files=_photo_files(),
        headers=headers,
    )

    assert res.status_code == 200
    assert res.content == b"fake-final-mp4-bytes"
    assert len(captured["image_paths"]) == 1
    assert captured["video_path"] is None


async def test_generate_room_tour_calls_pipeline_with_video_and_returns_video(
    client, tenant, with_fake_tts_provider, monkeypatch
):
    from src.video.paths import WORKSPACE_ROOT

    captured = {}

    async def fake_generate_room_tour(
        *, llm, model, property_info, image_paths, video_path=None, tts_provider, bgm_path=None, job_dir=None
    ):
        captured["image_paths"] = image_paths
        captured["video_path"] = video_path
        out = WORKSPACE_ROOT / job_dir / "final.mp4"
        out.write_bytes(b"fake-final-mp4-bytes")
        return out

    monkeypatch.setattr("src.api.routes.room_tour.generate_room_tour", fake_generate_room_tour)

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.post(
        "/api/room-tour/generate",
        data={"property_info": "テスト物件、2LDK"},
        files=_video_files(),
        headers=headers,
    )

    assert res.status_code == 200
    assert res.content == b"fake-final-mp4-bytes"
    assert captured["image_paths"] == []
    assert captured["video_path"] is not None
