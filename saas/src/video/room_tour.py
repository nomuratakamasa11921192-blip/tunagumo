"""実際の写真・動画からルームツアー動画を作る(ユーザー方針: 素材は必ず顧客が
アップロードした実写のみ。Higgsfieldに部屋の中身を想像させない)。

工程: 台本生成(script.py) → ナレーション音声合成(tts.py) → 下地映像
(写真ならffmpeg_ops.photos_to_videoでスライドショー、生動画ならjump_cut_silenceで
無音区間を除いたもの。どちらもAIは映像内容に一切関与しない) → ナレーション+BGM合成
(mix_audio、元の動画音声はナレーションに置き換わる) → 台本テキストから字幕を焼き込む
(STTを使わないので字幕精度が高く、追加コストもかからない)。

生動画を使う場合、下地映像の尺は動画側の長さで決まる(ナレーションとは独立)。
ナレーションと動画の長さが大きく異なる場合、mix_audioの-shortestにより短い方に
合わせて切り詰められる(既知の制約、必要なら将来ループ/トリム調整を追加する)。
"""

import uuid
from pathlib import Path

from src.agent.llm import StructuredLLM
from src.video.ffmpeg_ops import (
    FFmpegError,
    burn_subtitles,
    jump_cut_silence,
    mix_audio,
    photos_to_video,
    probe_duration,
)
from src.video.paths import WORKSPACE_ROOT
from src.video.schemas import VideoScript
from src.video.script import ScriptGenerationError, generate_script
from src.video.tts import TTSError, TTSProvider

ROOM_TOUR_WORKDIR = "room_tour_jobs"
MIN_SECONDS_PER_PHOTO = 1.5


class RoomTourError(Exception):
    pass


def new_job_dir() -> Path:
    job_dir = Path(ROOM_TOUR_WORKDIR) / uuid.uuid4().hex[:12]
    (WORKSPACE_ROOT / job_dir).mkdir(parents=True, exist_ok=True)
    return job_dir


def _fmt_timestamp(seconds: float) -> str:
    seconds = max(seconds, 0.0)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _script_to_srt(script: VideoScript, total_seconds: float) -> str:
    """各シーンのナレーション文字数に比例して尺を割り振り、簡易的なSRTを作る
    (音声認識(STT)を使わない分、正確な発話タイミングとはズレる可能性がある近似)。
    """
    scenes = [s for s in script.scenes if s.narration.strip()]
    if not scenes:
        return ""
    total_chars = sum(len(s.narration) for s in scenes) or 1

    lines: list[str] = []
    cursor = 0.0
    for i, scene in enumerate(scenes, start=1):
        duration = total_seconds * (len(scene.narration) / total_chars)
        start, end = cursor, cursor + duration
        cursor = end
        caption = scene.on_screen_text.strip() or scene.narration.strip()
        lines.append(str(i))
        lines.append(f"{_fmt_timestamp(start)} --> {_fmt_timestamp(end)}")
        lines.append(caption)
        lines.append("")
    return "\n".join(lines)


async def generate_room_tour(
    *,
    llm: StructuredLLM,
    model: str,
    property_info: str,
    image_paths: list[str | Path] | None = None,
    video_path: str | Path | None = None,
    tts_provider: TTSProvider,
    bgm_path: str | Path | None = None,
    job_dir: str | Path | None = None,
) -> Path:
    """property_infoから台本を作り、下地映像(video_pathがあれば生動画の無音カット後、
    無ければimage_paths=顧客がアップロードした実際の写真のスライドショー)に
    ナレーション・BGM・字幕を合成した動画を返す。image_paths/video_pathのどちらか
    一方は必須(video_path優先)。
    """
    image_paths = image_paths or []
    if not image_paths and video_path is None:
        raise RoomTourError("写真または動画のいずれかをアップロードしてください")

    workdir = Path(job_dir) if job_dir is not None else new_job_dir()

    try:
        script, _usage = await generate_script(llm=llm, model=model, topic=property_info)
    except ScriptGenerationError as e:
        raise RoomTourError(f"台本の生成に失敗しました: {e}") from e

    full_narration = "。".join(
        s.narration.strip().rstrip("。") for s in script.scenes if s.narration.strip()
    )
    if not full_narration:
        raise RoomTourError("台本からナレーションを生成できませんでした")

    try:
        audio_bytes = await tts_provider.synthesize(full_narration)
    except TTSError as e:
        raise RoomTourError(f"ナレーション音声の生成に失敗しました: {e}") from e

    narration_path_rel = workdir / "narration.mp3"
    (WORKSPACE_ROOT / narration_path_rel).write_bytes(audio_bytes)

    try:
        narration_duration = await probe_duration(narration_path_rel)
    except FFmpegError as e:
        raise RoomTourError(f"ナレーション音声の長さ取得に失敗しました: {e}") from e

    try:
        if video_path is not None:
            base_video = await jump_cut_silence(video_path, workdir / "base.mp4")
        else:
            seconds_per_photo = max(narration_duration / len(image_paths), MIN_SECONDS_PER_PHOTO)
            base_video = await photos_to_video(
                image_paths, workdir / "base.mp4", seconds_per_photo=seconds_per_photo
            )
        narrated = await mix_audio(
            base_video, narration_path_rel, workdir / "narrated.mp4", bgm_path=bgm_path
        )
    except FFmpegError as e:
        raise RoomTourError(f"映像・音声の合成に失敗しました: {e}") from e

    srt_text = _script_to_srt(script, narration_duration)
    if srt_text:
        srt_path = WORKSPACE_ROOT / (workdir / "captions.srt")
        srt_path.write_text(srt_text, encoding="utf-8")
        try:
            final = await burn_subtitles(narrated, workdir / "captions.srt", workdir / "final.mp4")
        except FFmpegError as e:
            raise RoomTourError(f"字幕の合成に失敗しました: {e}") from e
    else:
        final = narrated

    return final
