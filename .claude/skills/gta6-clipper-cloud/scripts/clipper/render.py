"""Make each clip: fetch only its video, line it up with the stream audio to the
frame, write exact captions, frame it 9:16, add the title bar, encode."""

import json

import numpy as np

from . import captions, fetch, hook, layout, scan
from .util import log, pcm, probe, run, slugify

PAD = 4.0   # extra seconds fetched on each side, trimmed off after alignment


def _envelope(x, rate=8000, hop=80):
    n = len(x) // hop
    return np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-9)


def locate(clip_file, audio_file, guess, audio_offset=0.0, search=30.0):
    """Where clip_file's first frame sits in the stream (seconds), found by matching
    its sound against the stream audio. Returns (seconds, confidence 0-1)."""
    a = _envelope(pcm(clip_file, 8000, 0, 20)).astype(np.float32)
    b_start = max(0.0, guess - audio_offset - search)
    b = _envelope(pcm(audio_file, 8000, b_start, 20 + 2 * search)).astype(np.float32)
    if len(a) < 200 or len(b) <= len(a):
        return guess, 0.0
    a = (a - a.mean()) / (a.std() + 1e-9)
    win = np.lib.stride_tricks.sliding_window_view(b, len(a))
    mu, sd = win.mean(axis=1), win.std(axis=1) + 1e-9
    corr = ((win - mu[:, None]) @ a) / (len(a) * sd)
    k = int(np.argmax(corr))
    return audio_offset + b_start + k / 100.0, float(corr[k])


def _video_args(cfg):
    if "videotoolbox" in cfg["encoder"]:
        return ["-c:v", cfg["encoder"], "-b:v", "10M", "-profile:v", "high"]
    return ["-c:v", cfg["encoder"], "-crf", str(cfg["crf"]), "-preset", cfg["preset"]]


def _esc(path):
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def prepare(url, args, audio_file, moment, index, workdir, cfg, language):
    """Fetch one clip's video, line it up with the stream audio, write its captions,
    and sample its frames. Nothing is encoded yet."""
    start, end = moment["start"], moment["end"]
    dur = end - start
    name = f"{index:02d}_{slugify(moment.get('title') or moment.get('hook_text'))}"
    log(f"[clip {index}] {moment.get('hook_text', '')}  ({dur:.0f} s)", "step")
    part = fetch.section(url, args, start - PAD, end + PAD, workdir / f"part_{index:02d}.mp4", cfg)
    info = probe(part)
    if not info["width"]:
        raise RuntimeError("the downloaded part has no picture")
    at, conf = locate(part, audio_file, start - PAD)
    offset = start - at if conf >= 0.5 else PAD
    offset = min(max(offset, 0.0), max(0.0, info["duration"] - dur))
    if conf < 0.5:
        log(f"  sound match weak ({conf:.2f}) -> trusting the requested times", "warn")
    words, _ = scan.words_from(cfg["caption_model"], pcm(part, 16000, offset, dur), language)
    return {"index": index, "name": name, "moment": moment, "part": part, "info": info,
            "offset": offset, "dur": dur, "words": words,
            "analysis": layout.analyze(part, offset, offset + dur, info)}


def learn(p, cfg, known):
    """Quiet pass: let every clip teach `known` where this stream's facecam sits."""
    layout.graph(p["part"], p["offset"], p["offset"] + p["dur"], cfg, p["info"],
                 known=known, analysis=p["analysis"], quiet=True)


def finish(p, outdir, workdir, cfg, known):
    """Frame, title, caption and encode one prepared clip."""
    m, dur, part, name = p["moment"], p["dur"], p["part"], p["name"]
    dest = outdir / f"{name}.mp4"
    log(f"[clip {p['index']}] framing + encoding", "step")
    ass = captions.build(p["words"], 0.0, dur, workdir / f"{name}.ass", cfg)
    chain, look, cam_h = layout.graph(part, p["offset"], p["offset"] + dur, cfg, p["info"],
                                      known=known, analysis=p["analysis"])
    chain += f";[vout]ass=f='{_esc(ass)}'[v]"
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin", "-ss", f"{p['offset']:.3f}", "-t", f"{dur:.3f}",
           "-i", str(part)]
    if m.get("hook_text"):
        try:
            png, _, hh = hook.render(m["hook_text"], cfg, workdir / f"{name}_hook.png")
            top = int(cam_h - hh / 2) if cam_h else int(cfg["output_height"] * cfg["hook_y"])
            cmd += ["-i", str(png)]
            chain = chain.replace("[v]", "[vh]") + f";[vh][1:v]overlay=(W-w)/2:{top}[v]"
        except (OSError, ValueError) as exc:
            log(f"  title bar skipped ({exc})", "warn")
    speech = "[0:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]" if cfg["loudnorm"] else "[0:a]anull[a]"
    cmd += ["-filter_complex", f"{chain};{speech}", "-map", "[v]", "-map", "[a]",
            "-c:a", "aac", "-b:a", cfg["audio_bitrate"], "-ar", "48000",
            *_video_args(cfg), "-movflags", "+faststart", str(dest)]
    run(cmd, timeout=1800)
    part.unlink(missing_ok=True)
    log(f"  -> {dest.name} ({look} layout)", "ok")
    return {
        "file": dest.name, "kind": m.get("kind"), "hook_text": m.get("hook_text"),
        "caption": m.get("caption"), "hashtags": m.get("hashtags", []),
        "score": m.get("score"), "reason": m.get("reason"), "layout": look,
        "stream_start": round(m["start"], 1), "stream_end": round(m["end"], 1), "duration": round(dur, 1),
    }


def post_sheet(outdir, stream, clips, summary):
    """POST.md: what to paste under each clip, plus where it came from."""
    lines = [f"# {stream['name']}: {stream['title']}", "", f"Stream: {stream['url']}", "",
             summary, ""]
    for c in clips:
        m, s = divmod(int(c["stream_start"]), 60)
        h, m = divmod(m, 60)
        lines += [f"## {c['file']}",
                  f"**{c['hook_text']}** · {c['kind']} · {c['duration']:.0f} s · score {c['score']}",
                  "", c.get("caption") or "", "", " ".join(c.get("hashtags") or []), "",
                  f"_Why: {c.get('reason') or ''}_",
                  f"_From the stream at {h}:{m:02d}:{s:02d}_", "", "---", ""]
    (outdir / "POST.md").write_text("\n".join(lines))
    (outdir / "clips.json").write_text(json.dumps({"stream": stream, "clips": clips}, indent=2))
