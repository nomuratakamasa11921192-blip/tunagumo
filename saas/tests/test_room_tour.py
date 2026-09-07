"""src/video/room_tour.py の検証。台本生成(generate_script)とTTSはフェイクに差し替え、
ffmpeg部分は実際に動かして検証する(test_video_ffmpeg_ops.pyと同じ方針)。
"""

import asyncio
import shutil
import uuid

import pytest

from src.video.paths import WORKSPACE_ROOT
from src.video.room_tour import RoomTourError, generate_room_tour
from src.video.schemas import VideoScene, VideoScript

pytestmark = pytest.mark.asyncio(loop_scope="session")

_created_workdirs: list[str] = []


def _workdir() -> str:
    d = f"pytest_room_tour_{uuid.uuid4().hex[:12]}"
    (WORKSPACE_ROOT / d).mkdir(parents=True, exist_ok=True)
    _created_workdirs.append(d)
    return d


@pytest.fixture(autouse=True)
def _cleanup_workdirs():
    yield
    while _created_workdirs:
        shutil.rmtree(WORKSPACE_ROOT / _created_workdirs.pop(), ignore_errors=True)


async def _run(cmd: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _out, err = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"setup command failed: {err.decode(errors='replace')[:500]}")


async def _make_test_photo(rel_path: str, *, color: str = "red") -> None:
    out = WORKSPACE_ROOT / rel_path
    out.parent.mkdir(parents=True, exist_ok=True)
    await _run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=640x480", "-frames:v", "1", "-update", "1", str(out)])


async def _fake_narration_audio_bytes(seconds: float = 2.0) -> bytes:
    tmp_name = f"pytest_tts_{uuid.uuid4().hex[:12]}.mp3"
    tmp_path = WORKSPACE_ROOT / tmp_name
    await _run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", str(tmp_path)])
    data = tmp_path.read_bytes()
    tmp_path.unlink()
    return data


class _FakeTTSProvider:
    def __init__(self, audio_bytes: bytes):
        self._audio_bytes = audio_bytes
        self.calls: list[str] = []

    async def synthesize(self, text, *, voice="alloy"):
        self.calls.append(text)
        return self._audio_bytes


class _FakeLLM:
    async def call_structured(self, *, model, system_prompt, user_message, output_model, max_tokens=2048):
        return (
            VideoScript(
                title="テスト物件のご案内",
                scenes=[
                    VideoScene(narration="リビングは日当たり良好です。", on_screen_text="リビング"),
                    VideoScene(narration="キッチンは収納が豊富です。", on_screen_text="キッチン"),
                ],
            ),
            {"input_tokens": 10, "output_tokens": 10},
        )


async def test_generate_room_tour_produces_video():
    d = _workdir()
    await _make_test_photo(f"{d}/photos/a.jpg", color="red")
    await _make_test_photo(f"{d}/photos/b.jpg", color="blue")
    audio_bytes = await _fake_narration_audio_bytes(2.0)

    out_path = await generate_room_tour(
        llm=_FakeLLM(),
        model="test-model",
        property_info="テスト物件、2LDK、駅徒歩5分",
        image_paths=[f"{d}/photos/a.jpg", f"{d}/photos/b.jpg"],
        tts_provider=_FakeTTSProvider(audio_bytes),
        job_dir=d,
    )

    assert out_path.exists()
    assert out_path.stat().st_size > 0


async def test_generate_room_tour_rejects_no_photos_and_no_video():
    with pytest.raises(RoomTourError):
        await generate_room_tour(
            llm=_FakeLLM(),
            model="test-model",
            property_info="テスト物件",
            image_paths=[],
            tts_provider=_FakeTTSProvider(b""),
        )


async def test_generate_room_tour_uses_video_when_provided():
    d = _workdir()
    video_rel = f"{d}/input.mp4"
    await _run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=3",
            "-shortest",
            str(WORKSPACE_ROOT / video_rel),
        ]
    )
    audio_bytes = await _fake_narration_audio_bytes(2.0)

    out_path = await generate_room_tour(
        llm=_FakeLLM(),
        model="test-model",
        property_info="テスト物件、2LDK",
        video_path=video_rel,
        tts_provider=_FakeTTSProvider(audio_bytes),
        job_dir=d,
    )

    assert out_path.exists()
    assert out_path.stat().st_size > 0


async def test_generate_room_tour_wraps_tts_failure():
    from src.video.tts import TTSError

    class _FailingTTS:
        async def synthesize(self, text, *, voice="alloy"):
            raise TTSError("音声合成に失敗しました")

    d = _workdir()
    await _make_test_photo(f"{d}/photos/a.jpg", color="red")

    with pytest.raises(RoomTourError):
        await generate_room_tour(
            llm=_FakeLLM(),
            model="test-model",
            property_info="テスト物件",
            image_paths=[f"{d}/photos/a.jpg"],
            tts_provider=_FailingTTS(),
            job_dir=d,
        )
