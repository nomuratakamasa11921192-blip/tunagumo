import httpx
import pytest

import src.core.higgsfield_client as higgsfield_client
from src.core.higgsfield_client import HiggsfieldClient, HiggsfieldError

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(higgsfield_client, "POLL_INTERVAL_SECONDS", 0.01)


def _completed_response(url="https://cdn.example.com/out.mp4"):
    return httpx.Response(200, json={"status": "completed", "result": {"url": url}})


def _client(handler):
    return HiggsfieldClient("key-id", "key-secret", transport=httpx.MockTransport(handler))


async def test_generate_video_draft_sends_plain_prompt_only():
    seen_bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/text-to-video"):
            import json

            seen_bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"request_id": "r1"})
        return _completed_response()

    client = _client(handler)
    url = await client.generate_video("make a video")
    assert url == "https://cdn.example.com/out.mp4"
    assert seen_bodies == [{"prompt": "make a video"}]


async def test_generate_video_with_reference_image_sends_start_image():
    seen_bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/text-to-video"):
            import json

            seen_bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"request_id": "r1"})
        return _completed_response()

    client = _client(handler)
    url = await client.generate_video(
        "make a video", reference_image_url="https://example.com/photo.jpg"
    )
    assert url == "https://cdn.example.com/out.mp4"
    assert seen_bodies[0] == {"prompt": "make a video", "start_image": "https://example.com/photo.jpg"}


async def test_generate_video_falls_back_when_start_image_rejected():
    """start_imageパラメータをHiggsfieldが拒否した場合、それを外して再試行すること。"""
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/text-to-video"):
            import json

            body = json.loads(request.content)
            attempts.append(body)
            if "start_image" in body:
                return httpx.Response(400, text="unknown field: start_image")
            return httpx.Response(200, json={"request_id": "r" + str(len(attempts))})
        return _completed_response()

    client = _client(handler)
    url = await client.generate_video(
        "make a video", reference_image_url="https://example.com/photo.jpg"
    )
    assert url == "https://cdn.example.com/out.mp4"
    assert attempts == [
        {"prompt": "make a video", "start_image": "https://example.com/photo.jpg"},
        {"prompt": "make a video"},
    ]


async def test_generate_video_high_quality_with_reference_falls_back_step_by_step():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/text-to-video"):
            import json

            body = json.loads(request.content)
            attempts.append(body)
            if "start_image" in body or "resolution" in body:
                return httpx.Response(400, text="rejected")
            return httpx.Response(200, json={"request_id": "r" + str(len(attempts))})
        return _completed_response()

    client = _client(handler)
    url = await client.generate_video(
        "make a video", quality="high", reference_image_url="https://example.com/photo.jpg"
    )
    assert url == "https://cdn.example.com/out.mp4"
    assert attempts == [
        {"prompt": "make a video", "start_image": "https://example.com/photo.jpg", "resolution": "1080p"},
        {"prompt": "make a video", "resolution": "1080p"},
        {"prompt": "make a video"},
    ]


async def test_generate_video_raises_when_all_attempts_fail():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/text-to-video"):
            return httpx.Response(400, text="always rejected")
        return _completed_response()

    client = _client(handler)
    with pytest.raises(HiggsfieldError):
        await client.generate_video("make a video", reference_image_url="https://example.com/photo.jpg")


async def test_edit_image_sends_image_references_first():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/edit"):
            import json

            attempts.append(json.loads(request.content))
            return httpx.Response(200, json={"request_id": "r1"})
        return _completed_response("https://cdn.example.com/staged.jpg")

    client = _client(handler)
    url = await client.edit_image("https://example.com/room.jpg", "add a sofa")
    assert url == "https://cdn.example.com/staged.jpg"
    assert attempts == [{"prompt": "add a sofa", "image_references": ["https://example.com/room.jpg"]}]


async def test_edit_image_falls_back_to_image_url_field():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/edit"):
            import json

            body = json.loads(request.content)
            attempts.append(body)
            if "image_references" in body:
                return httpx.Response(400, text="unknown field: image_references")
            return httpx.Response(200, json={"request_id": "r2"})
        return _completed_response("https://cdn.example.com/staged.jpg")

    client = _client(handler)
    url = await client.edit_image("https://example.com/room.jpg", "add a sofa")
    assert url == "https://cdn.example.com/staged.jpg"
    assert attempts == [
        {"prompt": "add a sofa", "image_references": ["https://example.com/room.jpg"]},
        {"prompt": "add a sofa", "image_url": "https://example.com/room.jpg"},
    ]


async def test_edit_image_raises_when_both_attempts_fail():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/edit"):
            return httpx.Response(404, text="not found")
        return _completed_response()

    client = _client(handler)
    with pytest.raises(HiggsfieldError):
        await client.edit_image("https://example.com/room.jpg", "add a sofa")


@pytest.mark.parametrize("kind", ["image", "video"])
async def test_completed_response_uses_documented_media_fields(kind):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"request_id": "r1"})
        output = {"url": f"https://cdn.example.com/{kind}"}
        return httpx.Response(200, json={
            "status": "completed",
            **({"images": [output]} if kind == "image" else {"video": output}),
        })

    client = _client(handler)
    generate = client.generate_image if kind == "image" else client.generate_video
    assert await generate("a bright living room") == f"https://cdn.example.com/{kind}"


@pytest.mark.parametrize("status", ["failed", "nsfw", "canceled"])
async def test_terminal_failure_does_not_submit_another_generation(status, monkeypatch):
    monkeypatch.setattr(higgsfield_client, "VIDEO_POLL_TIMEOUT_SECONDS", 0.02)
    submitted = []
    polls = []

    def handler(request):
        if request.method == "POST":
            submitted.append(request)
            return httpx.Response(200, json={"request_id": "r1"})
        polls.append(request)
        return httpx.Response(200, json={"status": status})

    with pytest.raises(HiggsfieldError):
        await _client(handler).generate_video("room", quality="high")
    assert len(submitted) == 1
    assert len(polls) == 1


@pytest.mark.parametrize("status_code", [401, 403, 404, 500])
async def test_non_parameter_error_is_not_retried(status_code):
    submitted = []

    def handler(request):
        submitted.append(request)
        return httpx.Response(status_code, text="request failed")

    with pytest.raises(HiggsfieldError):
        await _client(handler).generate_image("room")
    assert len(submitted) == 1


async def test_poll_failure_does_not_repeat_an_accepted_request():
    submitted = []

    def handler(request):
        if request.method == "POST":
            submitted.append(request)
            return httpx.Response(200, json={"request_id": "r1"})
        return httpx.Response(503, text="temporarily unavailable")

    with pytest.raises(HiggsfieldError):
        await _client(handler).generate_image("room")
    assert len(submitted) == 1
