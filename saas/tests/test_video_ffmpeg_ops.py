"""src/video/ffmpeg_ops.py の検証。実際にffmpegを動かして確認する
(docker/Dockerfileでffmpegをインストール済み)。外部ファイルに依存せず、
ffmpeg自身の testsrc/sine ジェネレータで使い捨ての合成メディアを作ってテストする。
"""

import asyncio
import shutil
import uuid

import pytest

from src.video.ffmpeg_ops import (
    FFmpegError,
    burn_subtitles,
    detect_silence,
    extract_audio,
    jump_cut_silence,
    mix_audio,
    mix_bgm_over_original_audio,
    photos_to_video,
    probe_duration,
)
from src.video.paths import WORKSPACE_ROOT, WorkspacePathError, ensure_in_workspace

pytestmark = pytest.mark.asyncio(loop_scope="session")


_created_workdirs: list[str] = []


def _workdir() -> str:
    d = f"pytest_{uuid.uuid4().hex[:12]}"
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


async def _make_test_video(rel_path: str, *, duration: float = 2.0, with_tone: bool = True) -> None:
    """testsrc(色のついた映像)+ 指定秒数の音声(トーンまたは無音)の動画を作る。"""
    out = WORKSPACE_ROOT / rel_path
    audio_source = f"sine=frequency=1000:duration={duration}" if with_tone else f"anullsrc=duration={duration}"
    await _run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=10",
            "-f", "lavfi", "-i", audio_source,
            "-shortest",
            str(out),
        ]
    )


async def _make_silence_tone_silence_video(rel_path: str) -> None:
    """無音1秒 -> トーン1秒 -> 無音1秒、の3秒動画(ジェットカットのテスト用)。"""
    out = WORKSPACE_ROOT / rel_path
    await _run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=10",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=44100",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-filter_complex",
            "[1:a]atrim=duration=1[a1];[2:a]atrim=duration=1[a2];[3:a]atrim=duration=1[a3];"
            "[a1][a2][a3]concat=n=3:v=0:a=1[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-t", "3",
            str(out),
        ]
    )


async def test_probe_duration_matches_generated_length():
    d = _workdir()
    await _make_test_video(f"{d}/in.mp4", duration=2.0)

    duration = await probe_duration(f"{d}/in.mp4")

    assert 1.8 <= duration <= 2.2


async def test_ensure_in_workspace_rejects_path_traversal():
    with pytest.raises(WorkspacePathError):
        ensure_in_workspace("../outside.mp4")
    with pytest.raises(WorkspacePathError):
        ensure_in_workspace("/etc/passwd")


async def test_ensure_in_workspace_accepts_relative_path_inside():
    resolved = ensure_in_workspace("some/nested/file.mp4")
    assert resolved.is_relative_to(WORKSPACE_ROOT)


async def test_mix_audio_replaces_narration_track():
    d = _workdir()
    await _make_test_video(f"{d}/video.mp4", duration=2.0, with_tone=False)  # 映像のみ(音声は無音)
    # ナレーション用の短い音声を別途作る
    await _run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            f"{WORKSPACE_ROOT / d}/narration.mp3",
        ]
    )

    out_path = await mix_audio(f"{d}/video.mp4", f"{d}/narration.mp3", f"{d}/mixed.mp4")

    assert out_path.exists()
    duration = await probe_duration(f"{d}/mixed.mp4")
    assert 1.7 <= duration <= 2.3


async def test_burn_subtitles_produces_output_file():
    d = _workdir()
    await _make_test_video(f"{d}/video.mp4", duration=2.0)

    srt_path = WORKSPACE_ROOT / d / "sub.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nテスト字幕\n",
        encoding="utf-8",
    )

    out_path = await burn_subtitles(f"{d}/video.mp4", f"{d}/sub.srt", f"{d}/with_subs.mp4")

    assert out_path.exists()
    assert out_path.stat().st_size > 0


async def test_detect_silence_finds_silent_segment():
    d = _workdir()
    await _make_test_video(f"{d}/silent.mp4", duration=2.0, with_tone=False)

    silences = await detect_silence(f"{d}/silent.mp4", min_duration=0.3)

    assert len(silences) >= 1
    start, end = silences[0]
    assert start < end


async def test_detect_silence_finds_none_when_tone_plays_throughout():
    d = _workdir()
    await _make_test_video(f"{d}/tone.mp4", duration=2.0, with_tone=True)

    silences = await detect_silence(f"{d}/tone.mp4", min_duration=0.3)

    assert silences == []


async def test_jump_cut_silence_shortens_video_with_silent_middle():
    d = _workdir()
    await _make_silence_tone_silence_video(f"{d}/stt.mp4")
    original_duration = await probe_duration(f"{d}/stt.mp4")
    assert 2.7 <= original_duration <= 3.3

    out_path = await jump_cut_silence(
        f"{d}/stt.mp4", f"{d}/cut.mp4", min_duration=0.3, padding=0.05
    )

    cut_duration = await probe_duration(f"{d}/cut.mp4")
    # 前後の無音(合計約2秒)が(paddingを除いて)削られ、元より明確に短くなっていること
    assert cut_duration < original_duration - 0.5


async def test_ffmpeg_error_on_nonexistent_input():
    d = _workdir()
    with pytest.raises(FFmpegError):
        await probe_duration(f"{d}/does_not_exist.mp4")


async def test_extract_audio_produces_wav_file():
    d = _workdir()
    await _make_test_video(f"{d}/video.mp4", duration=2.0, with_tone=True)

    out_path = await extract_audio(f"{d}/video.mp4", f"{d}/audio.wav")

    assert out_path.exists()
    assert out_path.stat().st_size > 0
    duration = await probe_duration(f"{d}/audio.wav")
    assert 1.7 <= duration <= 2.3


async def _make_test_photo(rel_path: str, *, color: str = "red") -> None:
    out = WORKSPACE_ROOT / rel_path
    await _run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s=640x480",
            "-frames:v", "1", "-update", "1",
            str(out),
        ]
    )


async def test_photos_to_video_produces_slideshow_of_expected_duration():
    d = _workdir()
    await _make_test_photo(f"{d}/a.jpg", color="red")
    await _make_test_photo(f"{d}/b.jpg", color="blue")
    await _make_test_photo(f"{d}/c.jpg", color="green")

    out_path = await photos_to_video(
        [f"{d}/a.jpg", f"{d}/b.jpg", f"{d}/c.jpg"], f"{d}/slideshow.mp4", seconds_per_photo=1.0
    )

    assert out_path.exists()
    duration = await probe_duration(f"{d}/slideshow.mp4")
    assert 2.5 <= duration <= 3.5  # 3枚 x 1秒 ≒ 3秒


async def test_photos_to_video_rejects_empty_list():
    d = _workdir()
    with pytest.raises(FFmpegError):
        await photos_to_video([], f"{d}/out.mp4")


async def test_mix_bgm_over_original_audio_keeps_video_duration():
    d = _workdir()
    await _make_test_video(f"{d}/video.mp4", duration=2.0, with_tone=True)
    await _run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=10",
            f"{WORKSPACE_ROOT / d}/bgm.mp3",
        ]
    )

    out_path = await mix_bgm_over_original_audio(f"{d}/video.mp4", f"{d}/bgm.mp3", f"{d}/mixed.mp4")

    assert out_path.exists()
    # BGMのほうが長い(10秒)が、元動画の尺(2秒)に揃えられていること(-shortest)
    duration = await probe_duration(f"{d}/mixed.mp4")
    assert 1.7 <= duration <= 2.3
