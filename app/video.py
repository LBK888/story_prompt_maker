from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any


class VideoToolError(RuntimeError):
    pass


async def _run(command: list[str], timeout: int = 3600) -> tuple[str, str]:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=creationflags,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise VideoToolError(f"Command timed out: {command[0]}")
    if process.returncode:
        raise VideoToolError(stderr.decode(errors="replace")[-4000:])
    return stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def probe_duration(ffprobe: Path, video: Path) -> float:
    stdout, _ = await _run([
        str(ffprobe), "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(video),
    ])
    try:
        return float(json.loads(stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise VideoToolError(f"Cannot read duration for {video}") from exc


async def extract_last_frame(ffmpeg: Path, video: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        str(ffmpeg), "-y", "-sseof", "-0.08", "-i", str(video),
        "-frames:v", "1", "-q:v", "2", str(destination),
    ])
    if not destination.is_file():
        raise VideoToolError(f"FFmpeg did not create {destination}")
    return destination


async def concatenate(
    ffmpeg: Path,
    videos: list[Path],
    destination: Path,
    width: int,
    height: int,
    fps: int = 24,
) -> Path:
    if not videos:
        raise VideoToolError("No videos selected for concatenation")
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [str(ffmpeg), "-y"]
    for video in videos:
        command.extend(["-i", str(video)])
    filter_parts: list[str] = []
    concat_inputs: list[str] = []
    for index in range(len(videos)):
        filter_parts.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[v{index}]"
        )
        filter_parts.append(
            f"[{index}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
        )
        concat_inputs.extend([f"[v{index}]", f"[a{index}]"])
    filter_parts.append("".join(concat_inputs) + f"concat=n={len(videos)}:v=1:a=1[v][a]")
    command.extend([
        "-filter_complex", ";".join(filter_parts),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(destination),
    ])
    await _run(command, timeout=7200)
    return destination

