"""Downloads with yt-dlp. The whole stream is fetched as AUDIO only; video is fetched
only for the few minutes that become clips. A 6-hour stream costs ~300 MB, not ~10 GB."""

import glob
import json
import shutil
from pathlib import Path

from .util import log, run


def base_args(url, cookies=None, cookies_file=None):
    """JS runtime for YouTube (else yt-dlp quietly drops good formats) + browser cookies."""
    args = []
    if "youtu" in url and not shutil.which("deno"):
        for runtime in ("node", "bun"):
            path = shutil.which(runtime)
            if path:
                args += ["--js-runtimes", f"{runtime}:{path}"]
                break
    if cookies_file:
        args += ["--cookies", cookies_file]
    elif cookies:
        args += ["--cookies-from-browser", cookies]
    return args


def info(url, args, workdir):
    """Stream metadata (title, channel, length, live status). Cached."""
    cache = workdir / "info.json"
    if cache.exists():
        return json.loads(cache.read_text())
    raw = run(["yt-dlp", "-J", "--no-playlist", *args, url], timeout=180)
    full = json.loads(raw)
    keep = {k: full.get(k) for k in (
        "id", "title", "channel", "uploader", "duration", "view_count", "live_status",
        "was_live", "is_live", "webpage_url", "extractor_key", "timestamp", "release_timestamp",
    )}
    keep["name"] = keep.get("channel") or keep.get("uploader") or "stream"
    cache.write_text(json.dumps(keep, indent=2))
    return keep


def audio(url, args, workdir):
    """The whole stream's audio. Reused if already downloaded. (Always the full audio:
    YouTube throttles partial downloads, so a slice is slower than the whole thing.)"""
    done = [p for p in workdir.glob("audio.*") if not p.name.endswith((".part", ".ytdl"))]
    if done:
        log(f"reusing downloaded audio ({done[0].stat().st_size / 1e6:.0f} MB)", "ok")
        return done[0]
    log("downloading the stream's audio (video comes later, only for the clips)...", "step")
    run(["yt-dlp", "--no-playlist", "-f", "ba/wa/w", "-o", str(workdir / "audio.%(ext)s"),
         "--newline", "-N", "4", *args, url], timeout=7200)
    done = [p for p in workdir.glob("audio.*") if not p.name.endswith((".part", ".ytdl"))]
    if not done:
        raise RuntimeError("yt-dlp finished but no audio file appeared")
    log(f"audio: {done[0].stat().st_size / 1e6:.0f} MB", "ok")
    return done[0]


def section(url, args, start, end, dest, cfg):
    """Video for one clip only: [start, end] seconds of the stream."""
    if dest.exists() and dest.stat().st_size > 10000:
        return dest
    h = cfg["max_height"]
    for leftover in glob.glob(str(dest) + "*"):
        Path(leftover).unlink(missing_ok=True)
    run([
        "yt-dlp", "--no-playlist",
        "-f", f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b",
        "--merge-output-format", "mp4",
        "--download-sections", f"*{start:.2f}-{end:.2f}",
        "-o", str(dest), *args, url,
    ], timeout=1800)
    if not dest.exists():
        raise RuntimeError(f"no video came back for {start:.0f}-{end:.0f}s")
    return dest
