"""Listen to the whole stream: a fast transcript, a loudness curve (screams, hype)
and, on YouTube replays, how fast chat was moving. All local, all free."""

import json
import subprocess

import threading

import numpy as np

from .util import log, pcm, run

_MODELS = {}
_LOCK = threading.Lock()


def _model(name):
    """One Whisper model per size, shared between streams processed in parallel."""
    with _LOCK:
        if name not in _MODELS:
            from faster_whisper import WhisperModel
            log(f"loading Whisper '{name}' (the first run downloads it)...", "step")
            _MODELS[name] = WhisperModel(name, device="cpu", compute_type="int8")
        return _MODELS[name]


def words_from(model_name, audio_array, language, offset=0.0):
    np.seterr(divide="ignore", over="ignore", invalid="ignore")
    segments, info = _model(model_name).transcribe(
        audio_array, language=language, word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500}, beam_size=1 if model_name == "tiny" else 5,
        condition_on_previous_text=False,
    )
    out = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                out.append({"s": round(w.start + offset, 2), "e": round(w.end + offset, 2), "w": text})
    return out, info.language


def transcript(audio_path, lo, hi, workdir, cfg):
    """Transcript of stream seconds [lo, hi), block by block, in stream time. Each block
    is cached, so a stopped run resumes where it stopped."""
    cache = workdir / "scan.json"
    if cache.exists():
        data = json.loads(cache.read_text())
        log(f"reusing transcript ({len(data['words'])} words)", "ok")
        return data
    block = cfg["scan_block_minutes"] * 60
    words, language = [], cfg["language"]
    starts = list(np.arange(lo, hi, block))
    log(f"listening to {(hi - lo) / 60:.0f} min of stream in {len(starts)} block(s)...", "step")
    for i, t in enumerate(starts):
        part = workdir / f"scan_{i:03d}.json"
        if part.exists():
            got = json.loads(part.read_text())
        else:
            arr = pcm(audio_path, 16000, start=float(t), duration=float(min(block, hi - t)))
            got, lang = words_from(cfg["scan_model"], arr, language, offset=float(t))
            language = language or lang       # detect once, then keep it
            part.write_text(json.dumps(got))
        words += got
        log(f"  {(min(t + block, hi) - lo) / 60:.0f}/{(hi - lo) / 60:.0f} min heard", "info")
    if not words:
        raise RuntimeError("no speech found in this stream")
    data = {"words": words, "language": language}
    cache.write_text(json.dumps(data))
    for p in workdir.glob("scan_*.json"):
        p.unlink()
    return data


def loudness(audio_path, workdir):
    """Loudness per second in dB, streamed through ffmpeg so it never loads the whole file."""
    cache = workdir / "loud.json"
    if cache.exists():
        return json.loads(cache.read_text())
    rate = 8000
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(audio_path), "-vn", "-ac", "1",
         "-ar", str(rate), "-f", "s16le", "-"], stdout=subprocess.PIPE)
    out = []
    while True:
        buf = proc.stdout.read(rate * 2 * 60)   # one minute at a time
        if not buf:
            break
        x = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
        n = len(x) // rate
        if n:
            sec = x[: n * rate].reshape(n, rate)
            out += (20 * np.log10(np.sqrt((sec ** 2).mean(axis=1)) + 1e-6)).round(1).tolist()
    proc.wait()
    cache.write_text(json.dumps(out))
    return out


def relative(values, window=120):
    """How far each second sits above the median of the 2 minutes around it."""
    v = np.asarray(values, dtype=np.float32)
    if len(v) < 5:
        return np.zeros_like(v)
    half = window // 2
    padded = np.pad(v, (half, half), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, window + 1)[: len(v)]
    return v - np.median(windows[:, ::4], axis=1)


def chat_rate(url, args, workdir, cfg, stream_info):
    """Messages per 5 s from a YouTube live replay, or None. Best effort, time-boxed."""
    if not cfg.get("chat") or "youtube" not in (stream_info.get("extractor_key") or "").lower():
        return None
    if stream_info.get("live_status") not in ("was_live", "post_live"):
        return None
    cache = workdir / "chat.json"
    if cache.exists():
        return json.loads(cache.read_text())
    log("reading the chat replay (how hard chat went off, minute by minute)...", "step")
    try:
        run(["yt-dlp", "--skip-download", "--write-subs", "--sub-langs", "live_chat",
             "-o", str(workdir / "chat"), *args, url], timeout=cfg["chat_timeout"])
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        log(f"chat replay not used ({str(exc).splitlines()[0][:80]})", "warn")
    files = list(workdir.glob("chat*.live_chat.json*"))
    if not files:
        return None
    buckets = {}
    with open(files[0], encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                item = json.loads(line)["replayChatItemAction"]
                ms = int(item.get("videoOffsetTimeMsec", 0))
            except (ValueError, KeyError, TypeError):
                continue
            buckets[ms // 5000] = buckets.get(ms // 5000, 0) + len(item.get("actions", []))
    for f in files:
        f.unlink()
    if not buckets:
        return None
    rate = [0] * (max(buckets) + 1)
    for k, n in buckets.items():
        rate[k] = n
    cache.write_text(json.dumps(rate))
    log(f"chat replay: {sum(rate):,} messages", "ok")
    return rate
