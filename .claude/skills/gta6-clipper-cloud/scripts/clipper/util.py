"""Shared helpers: running commands, decoding audio, logging, timing."""

import json
import os
import shutil
import subprocess
import sys
import threading
import time

import numpy as np

_START = time.time()
_PRINT = threading.Lock()
_local = threading.local()


def set_tag(tag):
    """Prefix every log line from this thread (one thread per stream)."""
    _local.tag = tag


def log(msg, level="info"):
    mark = {
        "info": "\033[36m·\033[0m",
        "ok": "\033[32m✓\033[0m",
        "warn": "\033[33m!\033[0m",
        "err": "\033[31m✗\033[0m",
        "step": "\033[35m▸\033[0m",
    }.get(level, "·")
    el = time.time() - _START
    tag = getattr(_local, "tag", "")
    tag = f"\033[1m{tag}\033[0m " if tag else ""
    with _PRINT:
        print(f"{mark} [{int(el // 60):02d}:{int(el % 60):02d}] {tag}{msg}", flush=True)


def die(msg):
    log(msg, "err")
    sys.exit(1)


def run(cmd, capture=True, check=True, timeout=None):
    """Run a command. Returns stdout text (or '' when not capturing)."""
    proc = subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        tail = "\n".join(detail.splitlines()[-15:])
        raise RuntimeError(f"command failed ({proc.returncode}): {cmd[0]}\n{tail}")
    return (proc.stdout or "") if capture else ""


def require(binary, hint=""):
    if shutil.which(binary) is None:
        die(f"'{binary}' not found. {hint}")


def clean_env():
    """Environment for child `claude -p` calls (works even when launched from Claude Code)."""
    env = dict(os.environ)
    for key in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(key, None)
    return env


def pcm(path, rate, start=None, duration=None):
    """Decode audio to mono float32 at `rate` Hz through ffmpeg (never touches disk)."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{max(0.0, start):.3f}"]
    cmd += ["-i", str(path)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode {path}: {proc.stderr.decode()[-300:]}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def probe(path):
    """{width, height, duration, fps} for a media file (width/height 0 if audio only)."""
    out = run(["ffprobe", "-v", "error", "-print_format", "json",
               "-show_streams", "-show_format", str(path)])
    data = json.loads(out)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    fps = 30.0
    if video:
        num, _, den = video.get("avg_frame_rate", "0/0").partition("/")
        try:
            fps = float(num) / float(den) if float(den) else 30.0
        except (ValueError, ZeroDivisionError):
            fps = 30.0
    return {
        "width": int(video["width"]) if video else 0,
        "height": int(video["height"]) if video else 0,
        "duration": float(data["format"].get("duration") or 0),
        "fps": fps or 30.0,
    }


def hhmmss(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def minutes(seconds):
    """'4 h 09 min' / '12 min' / '40 s' — for the human summary."""
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    h, m = divmod(seconds // 60, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def slugify(text, limit=48):
    keep = []
    for ch in (text or "").lower():
        if ch.isalnum():
            keep.append(ch)
        elif ch in " -_" and (not keep or keep[-1] != "-"):
            keep.append("-")
    return "".join(keep).strip("-")[:limit] or "clip"


def even(n):
    n = int(round(n))
    return n - (n % 2)


class Timer:
    """Records how long each stage took, for the end-of-run summary."""

    def __init__(self):
        self.stages = {}
        self.t0 = time.time()

    def stage(self, name):
        timer = self

        class _Ctx:
            def __enter__(self):
                self.start = time.time()

            def __exit__(self, *exc):
                timer.stages[name] = timer.stages.get(name, 0) + time.time() - self.start

        return _Ctx()

    def total(self):
        return time.time() - self.t0
