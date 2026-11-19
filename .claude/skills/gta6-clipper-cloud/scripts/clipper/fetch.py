"""Downloads with yt-dlp. The whole stream is fetched as AUDIO only; video is fetched
only for the few minutes that become clips. A 6-hour stream costs ~300 MB, not ~10 GB."""

import glob
import json
import shutil
from pathlib import Path

from .util import log, run

import urllib.request
from concurrent.futures import ThreadPoolExecutor


def _hls_url(url, fmt, args):
    out = run(["yt-dlp", "--no-playlist", "-f", fmt, "--get-url", *args, url], timeout=300)
    text = out if isinstance(out, str) else getattr(out, "stdout", "")
    return [l for l in text.splitlines() if l.startswith("http")][0]


def _hls_section(m3u8, start, end, dest, audio_only=False):
    """Fetch only the HLS segments covering [start, end] and cut them exactly. Works on
    Twitch VODs that are still live, where ffmpeg would follow the playlist in real time."""
    base = m3u8.rsplit("/", 1)[0]
    text = urllib.request.urlopen(m3u8, timeout=60).read().decode()
    segs, t, dur = [], 0.0, None
    for line in text.splitlines():
        if line.startswith("#EXTINF:"):
            dur = float(line[8:].split(",")[0])
        elif line and not line.startswith("#") and dur is not None:
            if t + dur > start - 1 and t < end + 1:
                segs.append((t, line if line.startswith("http") else f"{base}/{line}"))
            t += dur
            dur = None
    if not segs:
        raise RuntimeError(f"no stream segments cover {start:.0f}-{end:.0f}s")
    tmp = dest.parent / (dest.stem + "_segs")
    tmp.mkdir(parents=True, exist_ok=True)

    def get(i_seg):
        i, (_, u) = i_seg
        f = tmp / f"{i:05d}.ts"
        for _ in range(4):
            try:
                f.write_bytes(urllib.request.urlopen(u, timeout=60).read())
                return f
            except Exception:
                continue
        raise RuntimeError(f"segment download failed: {u}")

    with ThreadPoolExecutor(8) as pool:
        files = list(pool.map(get, enumerate(segs)))
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{f}'\n" for f in files))
    if audio_only:      # the whole stream, untouched
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-vn", "-c:a", "copy", str(dest)], timeout=1800)
    else:
        off = start - segs[0][0]
        run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-ss", f"{off:.3f}", "-t", f"{end - start:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", str(dest)], timeout=1800)
    shutil.rmtree(tmp, ignore_errors=True)


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
    if "twitch.tv" in url:
        # Segments in parallel: fast, and a still-live VOD doesn't download in real time.
        for leftover in workdir.glob("audio.*"):
            leftover.unlink(missing_ok=True)
        dest = workdir / "audio.m4a"
        _hls_section(_hls_url(url, "Audio_Only/ba/worst", args), 0, 10 ** 9, dest, audio_only=True)
        log(f"audio: {dest.stat().st_size / 1e6:.0f} MB", "ok")
        return dest
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
    if "twitch.tv" in url:
        m3u8 = _hls_url(url, f"b[height<={h}]/b", args)
        _hls_section(m3u8, start, end, dest)
        if dest.exists() and dest.stat().st_size > 10000:
            _check_height(dest, h)
            return dest
    run([
        "yt-dlp", "--no-playlist",
        "-f", f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b",
        "--merge-output-format", "mp4",
        "--download-sections", f"*{start:.2f}-{end:.2f}",
        "-o", str(dest), *args, url,
    ], timeout=1800)
    if not dest.exists():
        raise RuntimeError(f"no video came back for {start:.0f}-{end:.0f}s")
    _check_height(dest, h)
    return dest


def _check_height(path, want):
    """Clips should be cut from 1080p: say so loudly when the source is lower."""
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=height", "-of", "csv=p=0", str(path)], check=False).strip()
    if out.isdigit() and int(out) < want:
        log(f"source video is only {out}p (wanted {want}p): this stream isn't available "
            f"in {want}p, the clip will be softer", "warn")
