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


def _recording_handler(calls, reject_image=False):
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls.append((request.url.path, json.loads(request.content)))
            if reject_image and request.url.path.endswith("/image-to-video"):
                return httpx.Response(422, text="image_url could not be fetched")
            return httpx.Response(200, json={"request_id": "r" + str(len(calls))})
        return _completed_response()

    return handler


async def test_generate_video_draft_without_image_uses_hailuo_text_to_video():
    calls = []
    url = await _client(_recording_handler(calls)).generate_video("make a video")
    assert url == "https://cdn.example.com/out.mp4"
    assert calls == [("/minimax/hailuo-2.3/standard/text-to-video", {"prompt": "make a video", "duration": 6})]


async def test_generate_video_draft_with_image_uses_kling_standard_image_to_video():
    calls = []
    await _client(_recording_handler(calls)).generate_video(
        "make a video", reference_image_url="https://example.com/photo.jpg"
    )
    assert calls == [(
        "/kling-video/v2.5-turbo/standard/image-to-video",
        {"prompt": "make a video", "image_url": "https://example.com/photo.jpg", "duration": 5},
    )]


async def test_generate_video_high_uses_kling_pro():
    calls = []
    client = _client(_recording_handler(calls))
    await client.generate_video("make a video", quality="high")
    await client.generate_video("make a video", quality="high", reference_image_url="https://example.com/p.jpg")
    assert [path for path, _ in calls] == [
        "/kling-video/v2.5-turbo/pro/text-to-video",
        "/kling-video/v2.5-turbo/pro/image-to-video",
    ]


async def test_generate_video_falls_back_to_text_when_image_rejected():
    """画像URLを受け付けない場合、同じ画質のtext-to-videoで作り直すこと。"""
    calls = []
    url = await _client(_recording_handler(calls, reject_image=True)).generate_video(
        "make a video", reference_image_url="https://example.com/photo.jpg"
    )
    assert url == "https://cdn.example.com/out.mp4"
    assert [path for path, _ in calls] == [
        "/kling-video/v2.5-turbo/standard/image-to-video",
        "/minimax/hailuo-2.3/standard/text-to-video",
    ]


async def test_generate_video_raises_when_text_to_video_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(400, text="always rejected")
        return _completed_response()

    with pytest.raises(HiggsfieldError):
        await _client(handler).generate_video("make a video", reference_image_url="https://example.com/photo.jpg")


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
