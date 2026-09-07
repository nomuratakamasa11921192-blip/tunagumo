"""FFmpegをサブプロセスとして実行するラッパー(Phase 9-3)。

- コマンドはこのモジュールのコードでのみ組み立てる。LLMの出力(台本のテキスト等)を
  コマンド引数の構造(オプション名やパス)には使わない。テキスト自体はファイル経由で
  渡す(字幕ファイル・音声ファイルなど)ので、引数インジェクションの余地がない。
- shell=Trueは使わない(create_subprocess_exec、引数はリストで渡す。シェルを経由しない
  ため、パスや文字列にシェルメタ文字が含まれていても解釈されない)。
- 入出力パスはworkspace/配下に固定する(src/video/paths.py参照)。
- ジョブにタイムアウトを設定する(無限に回るエンコードを防ぐ、9-1: VPSはGPUがなく
  CPUエンコードのため遅い)。
"""

import asyncio
import re
from pathlib import Path

from src.video.paths import ensure_in_workspace

DEFAULT_TIMEOUT_SECONDS = 600  # 10分


class FFmpegError(Exception):
    pass


async def _run(cmd: str, args: list[str], *, timeout: float) -> bytes:
    process = await asyncio.create_subprocess_exec(
        cmd,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise FFmpegError(f"{cmd}がタイムアウトしました({timeout}秒)") from None

    if process.returncode != 0:
        raise FFmpegError(
            f"{cmd}が失敗しました(code={process.returncode}): {stderr.decode(errors='replace')[:500]}"
        )
    return stdout


async def probe_duration(video_path: str | Path, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> float:
    """動画/音声ファイルの長さ(秒)を返す。"""
    resolved = ensure_in_workspace(video_path)
    stdout = await _run(
        "ffprobe",
        [
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(resolved),
        ],
        timeout=timeout,
    )
    return float(stdout.decode().strip())


async def burn_subtitles(
    video_path: str | Path, srt_path: str | Path, out_path: str | Path, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> Path:
    video_path = ensure_in_workspace(video_path)
    srt_path = ensure_in_workspace(srt_path)
    out_path = ensure_in_workspace(out_path)

    # subtitlesフィルタの引数内でコロンはオプション区切りと衝突するためエスケープする
    escaped_srt = str(srt_path).replace(":", "\\:")
    await _run(
        "ffmpeg",
        ["-y", "-i", str(video_path), "-vf", f"subtitles={escaped_srt}", str(out_path)],
        timeout=timeout,
    )
    return out_path


async def mix_audio(
    video_path: str | Path,
    narration_path: str | Path,
    out_path: str | Path,
    *,
    bgm_path: str | Path | None = None,
    bgm_volume: float = 0.15,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    """動画にナレーション音声を合成する。bgm_pathを指定するとBGMも小音量で重ねる。"""
    video_path = ensure_in_workspace(video_path)
    narration_path = ensure_in_workspace(narration_path)
    out_path = ensure_in_workspace(out_path)

    if bgm_path is not None:
        bgm_path = ensure_in_workspace(bgm_path)
        await _run(
            "ffmpeg",
            [
                "-y",
                "-i", str(video_path),
                "-i", str(narration_path),
                "-i", str(bgm_path),
                "-filter_complex",
                f"[2:a]volume={bgm_volume}[bgm];[1:a][bgm]amix=inputs=2:duration=first[aout]",
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "copy",
                "-shortest",
                str(out_path),
            ],
            timeout=timeout,
        )
    else:
        await _run(
            "ffmpeg",
            [
                "-y",
                "-i", str(video_path),
                "-i", str(narration_path),
                "-map", "0:v",
                "-map", "1:a",
                "-c:v", "copy",
                "-shortest",
                str(out_path),
            ],
            timeout=timeout,
        )
    return out_path


async def photos_to_video(
    image_paths: list[str | Path],
    out_path: str | Path,
    *,
    seconds_per_photo: float = 3.0,
    width: int = 1280,
    height: int = 720,
    fps: int = 25,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    """静止画をパン・ズームしながら順番につなぐスライドショー動画を作る(ルームツアー
    動画生成の標準の下地映像、src/video/room_tour.py参照)。AIは映像内容に一切関与
    しない(実際にアップロードされた写真をそのまま使うだけ)。
    """
    if not image_paths:
        raise FFmpegError("画像が1枚も指定されていません")

    resolved_inputs = [ensure_in_workspace(p) for p in image_paths]
    out_path = ensure_in_workspace(out_path)

    args = ["-y"]
    for p in resolved_inputs:
        args += ["-loop", "1", "-framerate", str(fps), "-t", str(seconds_per_photo), "-i", str(p)]

    # zoompanのdは「入力フレーム1枚あたりの出力フレーム数」であり、動画全体の
    # フレーム数ではない(d=フレーム総数を渡すと、フレーム総数^2の長さになってしまう
    # 落とし穴がある)。入力側を-framerate/-tで既に必要なフレーム数ぶん用意しているので、
    # d=1(入力フレーム1枚→出力フレーム1枚、ズームは毎フレーム少しずつ進む)にする。
    filter_parts = []
    for i in range(len(resolved_inputs)):
        filter_parts.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,"
            f"zoompan=z='min(zoom+0.0015,1.2)':d=1:s={width}x{height}:fps={fps}[v{i}]"
        )
    concat_inputs = "".join(f"[v{i}]" for i in range(len(resolved_inputs)))
    filter_parts.append(f"{concat_inputs}concat=n={len(resolved_inputs)}:v=1:a=0[outv]")
    filter_complex = ";".join(filter_parts)

    # zoompanの出力はコンテナ側で明示的にフレームレートを指定しないと、
    # フレーム間隔が意図せず引き延ばされることがあるため-rで強制する。
    args += [
        "-filter_complex", filter_complex, "-map", "[outv]",
        "-r", str(fps), "-pix_fmt", "yuv420p", str(out_path),
    ]

    await _run("ffmpeg", args, timeout=timeout)
    return out_path


async def extract_audio(
    video_path: str | Path, out_path: str | Path, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> Path:
    """動画から音声トラックを抜き出す(9-4: 字幕自動生成のためのSTT入力を作る)。
    16kHzモノラルWAVに統一する(音声認識APIの入力として扱いやすい形式)。
    """
    video_path = ensure_in_workspace(video_path)
    out_path = ensure_in_workspace(out_path)
    await _run(
        "ffmpeg",
        ["-y", "-i", str(video_path), "-vn", "-ar", "16000", "-ac", "1", str(out_path)],
        timeout=timeout,
    )
    return out_path


async def mix_bgm_over_original_audio(
    video_path: str | Path,
    bgm_path: str | Path,
    out_path: str | Path,
    *,
    bgm_volume: float = 0.12,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    """動画の元の音声(顧客自身の声)はそのまま残し、BGMだけ小音量で重ねる
    (9-4: mix_audioは常にナレーション音声で元音声を置き換える設計のため、
    元音声を活かしたいリール編集用に別関数として用意する)。BGMは動画尺に
    足りない場合ループし、動画尺(-shortest)に合わせて切り詰める。
    """
    video_path = ensure_in_workspace(video_path)
    bgm_path = ensure_in_workspace(bgm_path)
    out_path = ensure_in_workspace(out_path)

    await _run(
        "ffmpeg",
        [
            "-y",
            "-i", str(video_path),
            "-stream_loop", "-1", "-i", str(bgm_path),
            "-filter_complex",
            f"[1:a]volume={bgm_volume}[bgm];[0:a][bgm]amix=inputs=2:duration=first[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-shortest",
            str(out_path),
        ],
        timeout=timeout,
    )
    return out_path


_SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)")


async def detect_silence(
    video_path: str | Path,
    *,
    noise_db: float = -30.0,
    min_duration: float = 0.5,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[tuple[float, float]]:
    """無音区間の(開始秒, 終了秒)のリストを返す(9-2: 無音カット(ジェットカット))。"""
    resolved = ensure_in_workspace(video_path)
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-i", str(resolved),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise FFmpegError(f"無音検出がタイムアウトしました({timeout}秒)") from None

    text = stderr.decode(errors="replace")
    starts = [float(m) for m in _SILENCE_START_RE.findall(text)]
    ends = [float(m) for m in _SILENCE_END_RE.findall(text)]
    return list(zip(starts, ends))


async def jump_cut_silence(
    video_path: str | Path,
    out_path: str | Path,
    *,
    noise_db: float = -30.0,
    min_duration: float = 0.5,
    padding: float = 0.1,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    """無音区間を検出し、その部分を取り除いてつなぎ直す(ジェットカット)。
    paddingぶんだけ無音の前後を残すことで、不自然な切れ方を防ぐ。
    """
    video_path = ensure_in_workspace(video_path)
    out_path = ensure_in_workspace(out_path)

    silences = await detect_silence(video_path, noise_db=noise_db, min_duration=min_duration, timeout=timeout)
    duration = await probe_duration(video_path, timeout=timeout)

    keep_segments: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in silences:
        cut_start = max(cursor, start + padding)
        cut_end = min(duration, end - padding)
        if cut_start > cursor:
            keep_segments.append((cursor, cut_start))
        cursor = max(cursor, cut_end)
    if cursor < duration:
        keep_segments.append((cursor, duration))

    if not keep_segments:
        raise FFmpegError("無音区間の検出結果、残す区間がありませんでした(全編が無音の可能性)")

    if len(keep_segments) == 1 and keep_segments[0] == (0.0, duration):
        # カットする無音区間がなかった: そのままコピーする
        await _run("ffmpeg", ["-y", "-i", str(video_path), "-c", "copy", str(out_path)], timeout=timeout)
        return out_path

    select_expr = "+".join(f"between(t\\,{s:.3f}\\,{e:.3f})" for s, e in keep_segments)
    await _run(
        "ffmpeg",
        [
            "-y",
            "-i", str(video_path),
            "-vf", f"select='{select_expr}',setpts=N/FRAME_RATE/TB",
            "-af", f"aselect='{select_expr}',asetpts=N/SR/TB",
            str(out_path),
        ],
        timeout=timeout,
    )
    return out_path
