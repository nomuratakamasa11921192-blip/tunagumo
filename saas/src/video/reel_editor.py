"""スマホの生動画から、リール(ショート動画)を自動編集する(2位)。

工程: 無音区間のジェットカット → 音声抽出 → 字幕自動生成(STT) → 字幕焼き込み → BGM合成。
各工程は src/video/ffmpeg_ops.py と src/video/stt.py の既存部品をそのまま組み合わせる
(新しいffmpegコマンド構築はここでは行わない)。
"""

import uuid
from pathlib import Path

from src.video.ffmpeg_ops import (
    FFmpegError,
    burn_subtitles,
    extract_audio,
    jump_cut_silence,
    mix_bgm_over_original_audio,
)
from src.video.paths import WORKSPACE_ROOT
from src.video.stt import STTError, STTProvider

REEL_WORKDIR = "reel_jobs"


class ReelEditError(Exception):
    pass


def new_job_dir() -> Path:
    """workspace/配下の新規ジョブディレクトリを作成し、workspace/相対パスを返す
    (アップロードされた動画の保存先と、editing_reel()の作業ディレクトリを同じにするため、
    ルート側が先に呼んでからedit_reel()にjob_dirとして渡す)。
    """
    job_dir = Path(REEL_WORKDIR) / uuid.uuid4().hex[:12]
    (WORKSPACE_ROOT / job_dir).mkdir(parents=True, exist_ok=True)
    return job_dir


async def edit_reel(
    input_video_path: str | Path,
    *,
    stt_provider: STTProvider,
    bgm_path: str | Path | None = None,
    job_dir: str | Path | None = None,
) -> Path:
    """input_video_pathはworkspace/配下の相対パス(またはworkspace/配下の絶対パス)。
    戻り値も同様にworkspace/配下の絶対パス。job_dirを渡さない場合は新規作成する。
    """
    workdir = Path(job_dir) if job_dir is not None else new_job_dir()

    try:
        trimmed = await jump_cut_silence(input_video_path, workdir / "trimmed.mp4")
        audio = await extract_audio(trimmed, workdir / "audio.wav")
    except FFmpegError as e:
        raise ReelEditError(f"動画のカット処理に失敗しました: {e}") from e

    audio_bytes = (WORKSPACE_ROOT / (workdir / "audio.wav")).read_bytes()
    try:
        srt_text = await stt_provider.transcribe_to_srt(audio_bytes, filename="audio.wav")
    except STTError as e:
        raise ReelEditError(f"字幕の自動生成に失敗しました: {e}") from e

    srt_path = WORKSPACE_ROOT / (workdir / "captions.srt")
    srt_path.write_text(srt_text, encoding="utf-8")

    try:
        captioned = await burn_subtitles(trimmed, workdir / "captions.srt", workdir / "captioned.mp4")
        if bgm_path is not None:
            final = await mix_bgm_over_original_audio(captioned, bgm_path, workdir / "final.mp4")
        else:
            final = captioned
    except FFmpegError as e:
        raise ReelEditError(f"字幕・BGMの合成に失敗しました: {e}") from e

    return final
