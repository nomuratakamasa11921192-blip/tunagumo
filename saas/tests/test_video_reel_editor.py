"""src/video/reel_editor.py の検証。実際にffmpegを動かし、STTだけ偽物に差し替える
(test_video_ffmpeg_ops.pyと同じ「使い捨ての合成メディアで検証する」方針)。
"""

import asyncio
import shutil
import uuid

import pytest

from src.video.ffmpeg_ops import probe_duration
from src.video.paths import WORKSPACE_ROOT
from src.video.reel_editor import ReelEditError, edit_reel

pytestmark = pytest.mark.asyncio(loop_scope="session")

_created_workdirs: list[str] = []


def _workdir() -> str:
    d = f"pytest_reel_{uuid.uuid4().hex[:12]}"
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


async def _make_test_video(rel_path: str, *, duration: float = 2.0) -> None:
    out = WORKSPACE_ROOT / rel_path
    await _run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=10",
            "-f", "lavfi", "-i", f"sine=frequency=1000:duration={duration}",
            "-shortest",
            str(out),
        ]
    )


class _FakeSTTProvider:
    def __init__(self, srt_text="1\n00:00:00,000 --> 00:00:01,000\nテスト\n"):
        self.srt_text = srt_text
        self.calls = 0

    async def transcribe_to_srt(self, audio_bytes, *, filename="audio.wav"):
        self.calls += 1
        assert audio_bytes  # 空データが渡っていないこと
        return self.srt_text


class _FailingSTTProvider:
    async def transcribe_to_srt(self, audio_bytes, *, filename="audio.wav"):
        from src.video.stt import STTError

        raise STTError("認識に失敗しました")


async def test_edit_reel_produces_output_without_bgm():
    d = _workdir()
    await _make_test_video(f"{d}/input.mp4", duration=2.0)
    stt = _FakeSTTProvider()

    out_path = await edit_reel(f"{d}/input.mp4", stt_provider=stt, job_dir=d)

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    assert stt.calls == 1


async def test_edit_reel_mixes_bgm_when_provided():
    d = _workdir()
    await _make_test_video(f"{d}/input.mp4", duration=2.0)
    await _run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=5",
            f"{WORKSPACE_ROOT / d}/bgm.mp3",
        ]
    )
    stt = _FakeSTTProvider()

    out_path = await edit_reel(
        f"{d}/input.mp4", stt_provider=stt, bgm_path=f"{d}/bgm.mp3", job_dir=d
    )

    assert out_path.exists()
    duration = await probe_duration(out_path)
    assert duration > 0


async def test_edit_reel_raises_reel_edit_error_when_stt_fails():
    d = _workdir()
    await _make_test_video(f"{d}/input.mp4", duration=2.0)

    with pytest.raises(ReelEditError):
        await edit_reel(f"{d}/input.mp4", stt_provider=_FailingSTTProvider(), job_dir=d)
