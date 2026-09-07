import pytest

from src.api.deps import get_stt_provider
from src.main import app

pytestmark = pytest.mark.asyncio(loop_scope="session")


class _FakeSTTProvider:
    async def transcribe_to_srt(self, audio_bytes, *, filename="audio.wav"):
        return "1\n00:00:00,000 --> 00:00:01,000\nテスト\n"


@pytest.fixture
def with_fake_stt_provider():
    app.dependency_overrides[get_stt_provider] = lambda: _FakeSTTProvider()
    yield
    app.dependency_overrides.pop(get_stt_provider, None)


async def test_edit_reel_requires_auth(client):
    files = {"file": ("video.mp4", b"fake video bytes", "video/mp4")}
    res = await client.post("/api/reel/edit", files=files)
    assert res.status_code == 401


async def test_edit_reel_without_openai_key_returns_402(client, tenant):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    files = {"file": ("video.mp4", b"fake video bytes", "video/mp4")}
    res = await client.post("/api/reel/edit", files=files, headers=headers)
    assert res.status_code == 402


async def test_edit_reel_rejects_unsupported_content_type(client, tenant, with_fake_stt_provider):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    files = {"file": ("video.avi", b"fake video bytes", "video/x-msvideo")}
    res = await client.post("/api/reel/edit", files=files, headers=headers)
    assert res.status_code == 400


async def test_edit_reel_rejects_unknown_bgm(client, tenant, with_fake_stt_provider):
    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    files = {"file": ("video.mp4", b"fake video bytes", "video/mp4")}
    res = await client.post("/api/reel/edit?bgm=does-not-exist", files=files, headers=headers)
    assert res.status_code == 400


async def test_edit_reel_calls_pipeline_and_returns_video(
    client, tenant, with_fake_stt_provider, monkeypatch
):
    from pathlib import Path

    from src.video.paths import WORKSPACE_ROOT

    async def fake_edit_reel(input_video_path, *, stt_provider, bgm_path=None, job_dir=None):
        out = WORKSPACE_ROOT / job_dir / "final.mp4"
        out.write_bytes(b"fake-final-mp4-bytes")
        return out

    monkeypatch.setattr("src.api.routes.reel.edit_reel", fake_edit_reel)

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    files = {"file": ("video.mp4", b"fake video bytes", "video/mp4")}
    res = await client.post("/api/reel/edit", files=files, headers=headers)

    assert res.status_code == 200
    assert res.content == b"fake-final-mp4-bytes"


async def test_bgm_options_lists_bundled_tracks(client, tenant, monkeypatch, tmp_path):
    bgm_dir = tmp_path / "bgm"
    bgm_dir.mkdir()
    (bgm_dir / "calm.mp3").write_bytes(b"fake-mp3")
    monkeypatch.setattr("src.api.routes.reel.BGM_DIR", bgm_dir)

    headers = {"Authorization": f"Bearer {tenant['api_key']}"}
    res = await client.get("/api/reel/bgm-options", headers=headers)

    assert res.status_code == 200
    ids = [o["id"] for o in res.json()]
    assert "calm" in ids
