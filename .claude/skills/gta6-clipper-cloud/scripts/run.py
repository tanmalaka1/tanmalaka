#!/usr/bin/env python3
"""gta6clip: GTA 6 streams in, ready-to-post vertical clips out.

  gta6clip URL [URL ...]        clip these streams (several run at the same time)
  gta6clip --find               find today's biggest GTA 6 streams and clip them
  gta6clip --find --list        only show what it would clip

Clips land in ~/gta6-clips/<date>/<streamer>__<title>/ with a POST.md
(caption + hashtags per clip). Re-running the same command resumes; nothing is redone.
"""

import argparse
import datetime
import json
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipper import config, fetch, find, moments, render, scan  # noqa: E402
from clipper.util import Timer, die, hhmmss, log, minutes, probe, require, set_tag, slugify  # noqa: E402


def parse(argv):
    p = argparse.ArgumentParser(prog="gta6clip", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("urls", nargs="*", help="stream / VOD links (YouTube, Twitch, Kick)")
    p.add_argument("--find", action="store_true", help="find today's biggest GTA 6 streams")
    p.add_argument("--list", action="store_true", help="with --find: only list them")
    p.add_argument("--streams", type=int, default=5, help="with --find: how many streams (default 5)")
    p.add_argument("--clips", type=int, help="clips per stream (default 3)")
    p.add_argument("--parallel", type=int, default=2, help="streams processed at once (default 2)")
    p.add_argument("--section", metavar="RANGE",
                   help='only part of the stream, e.g. "1:00:00-3:30:00" (long streams, re-runs)')
    p.add_argument("--cookies", choices=["chrome", "safari", "firefox", "brave", "edge"],
                   help="use your browser login (needed for Kick)")
    p.add_argument("--cookies-file", dest="cookies_file", metavar="FILE",
                   help="Netscape cookies.txt exported from your browser (cloud/phone: no browser here)")
    p.add_argument("--layout", choices=["auto", "stream", "face", "fit"])
    p.add_argument("--model", dest="claude_model", help="Claude model for picking, e.g. sonnet")
    p.add_argument("--min", dest="min_duration", type=int, help="shortest clip in seconds")
    p.add_argument("--max", dest="max_duration", type=int, help="longest clip in seconds")
    p.add_argument("--fast", action="store_true", help="Mac hardware encoding (much faster)")
    p.add_argument("--keep-audio", action="store_true", help="keep the stream audio after clipping")
    p.add_argument("--redo", action="store_true", help="pick the moments again (keeps the audio)")
    p.add_argument("--pick-only", dest="pick_only", action="store_true",
                   help="find and score moments, show the shortlist, make no clips")
    p.add_argument("--choose", metavar="N,N,...",
                   help="make clips only for these shortlist numbers (from --pick-only)")
    return p.parse_args(argv)


def seconds(stamp):
    total = 0.0
    for part in stamp.strip().split(":"):
        total = total * 60 + float(part)
    return total


def one_stream(url, cfg, opts):
    """Everything for one stream. Returns a summary dict (or raises)."""
    timer = Timer()
    args = fetch.base_args(url, opts.cookies, opts.cookies_file)
    key = re.sub(r"[^A-Za-z0-9_-]", "_", url.split("://")[-1])[-80:]
    work = config.HOME / "work" / key          # the stream's audio, loudness, chat
    work.mkdir(parents=True, exist_ok=True)

    with timer.stage("look up"):
        info = fetch.info(url, args, work)
    set_tag(info["name"][:18])
    if info.get("live_status") == "is_live":
        log("this stream is still live: clipping what has been streamed so far", "warn")

    with timer.stage("download audio"):
        audio = fetch.audio(url, args, work)
    length = probe(audio)["duration"]
    lo, hi = 0.0, length
    if opts.section:
        a, _, b = opts.section.lstrip("*").partition("-")
        lo, hi = seconds(a), min(seconds(b), length) if b else length
    pick_dir = work if not opts.section else work / f"part_{int(lo)}_{int(hi)}"
    pick_dir.mkdir(exist_ok=True)
    if opts.redo:
        for f in ("moments.json", "candidates.json", "shortlist.json"):
            (pick_dir / f).unlink(missing_ok=True)
    log(f"{info['title'][:70]}  ·  {minutes(length)} of stream"
        + (f", clipping {hhmmss(lo)}-{hhmmss(hi)}" if opts.section else ""), "ok")

    with timer.stage("listen"):
        heard = scan.transcript(audio, lo, hi, pick_dir, cfg)
        loud = scan.relative(scan.loudness(audio, work))
        chat = scan.chat_rate(url, args, work, cfg, info)
    stream = {"name": info["name"], "title": info["title"], "url": url}
    shortlist_file = pick_dir / "shortlist.json"
    if opts.choose:
        if not shortlist_file.exists():
            raise RuntimeError("no shortlist yet: run with --pick-only first")
        short = json.loads(shortlist_file.read_text())
        nums = [int(n) for n in re.findall(r"\d+", opts.choose)]
        bad = [n for n in nums if not 1 <= n <= len(short)]
        if bad:
            raise RuntimeError(f"no shortlist number(s) {bad}; the shortlist has 1-{len(short)}")
        (pick_dir / "moments.json").write_text(json.dumps([short[n - 1] for n in nums], indent=2))
    elif opts.pick_only:
        (pick_dir / "moments.json").unlink(missing_ok=True)
    with timer.stage("pick"):
        if opts.pick_only:
            cfg = dict(cfg, clips_per_stream=cfg.get("shortlist_size", 15))
        picked = moments.find(heard, loud, chat, pick_dir, cfg, stream)
    if opts.pick_only:
        picked = sorted(picked, key=lambda m: -float(m.get("score", 0)))
        shortlist_file.write_text(json.dumps(picked, indent=2))
        (pick_dir / "moments.json").unlink(missing_ok=True)
        print()
        log(f"shortlist: {len(picked)} moment(s), best first. Make clips with --choose N,N,...", "ok")
        for n, m in enumerate(picked, 1):
            print(f"  {n:>2}. [{int(float(m.get('score', 0)))}] {hhmmss(float(m['start']))} "
                  f"{float(m['end']) - float(m['start']):.0f}s {m.get('kind', '')}: {m.get('hook_text', '')}")
            if m.get("reason"):
                print(f"      {m['reason']}")
        set_tag("")
        return {"name": info["name"], "title": info["title"], "outdir": pick_dir, "clips": [],
                "summary": f"shortlist of {len(picked)} in {shortlist_file}"}

    day = datetime.date.today().isoformat()
    outdir = config.HOME / day / f"{slugify(info['name'], 24)}__{slugify(info['title'], 40)}"
    outdir.mkdir(parents=True, exist_ok=True)
    made, ready, known = [], [], {}
    with timer.stage("make clips"):
        for i, m in enumerate(picked, 1):
            try:
                ready.append(render.prepare(url, args, audio, m, i, pick_dir, cfg,
                                            heard.get("language")))
            except Exception as exc:
                log(f"[clip {i}] failed: {str(exc)[:300]}", "err")
        for p in ready:              # learn where this stream's facecam sits from every clip
            render.learn(p, cfg, known)
        for p in ready:
            try:
                made.append(render.finish(p, outdir, pick_dir, cfg, known))
            except Exception as exc:
                log(f"[clip {p['index']}] failed: {str(exc)[:300]}", "err")

    summary = (f"{minutes(hi - lo)} of stream -> {len(made)} clip(s) in {minutes(timer.total())} "
               f"(" + ", ".join(f"{k} {minutes(v)}" for k, v in timer.stages.items()) + ")")
    render.post_sheet(outdir, stream, made, summary)
    (outdir / "report.json").write_text(json.dumps({
        "stream": stream, "stream_seconds": round(hi - lo), "clips": len(made),
        "total_seconds": round(timer.total()),
        "stages_seconds": {k: round(v) for k, v in timer.stages.items()},
        "claude_parallel": cfg["claude_parallel"], "scan_model": cfg["scan_model"]}, indent=2))
    if not opts.keep_audio and made:
        audio.unlink(missing_ok=True)
    log(summary, "ok")
    set_tag("")
    return {"name": info["name"], "title": info["title"], "outdir": outdir, "clips": made,
            "summary": summary}


def main(argv=None):
    opts = parse(argv if argv is not None else sys.argv[1:])
    for tool, hint in (("yt-dlp", "run scripts/setup.sh"), ("ffmpeg", "brew install ffmpeg"),
                       ("claude", "install Claude Code")):
        require(tool, hint)
    cfg = config.load({
        "clips_per_stream": opts.clips, "layout": opts.layout, "claude_model": opts.claude_model,
        "min_duration": opts.min_duration, "max_duration": opts.max_duration,
        "encoder": "h264_videotoolbox" if opts.fast else None,
    })
    config.HOME.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(config.HOME).free / 1e9
    if free_gb < 2:
        log(f"only {free_gb:.1f} GB free on this disk; long streams need ~0.1 GB per hour", "warn")

    urls = list(opts.urls)
    if opts.find:
        log("finding today's biggest GTA 6 streams...", "step")
        found = find.find(cfg, limit=opts.streams)
        if not found:
            die("no GTA streams found right now. Add streamers to ~/gta6-clips/streamers.txt "
                "or paste links.")
        print()
        for i, s in enumerate(found, 1):
            live = "LIVE " if s["live"] else ""
            print(f"  {i}. {s['name'][:22]:22} {s['views']:>10,} {live}· {s['title'][:60]}  ({s['source']})")
            print(f"     {s['url']}")
        print()
        if opts.list:
            return
        urls += [s["url"] for s in found]
    if not urls:
        die("give me stream links, or use --find")

    results = []
    with ThreadPoolExecutor(max_workers=max(1, opts.parallel)) as pool:
        futures = {pool.submit(one_stream, u, cfg, opts): u for u in urls}
        for fut, url in futures.items():
            try:
                results.append(fut.result())
            except Exception as exc:
                msg = str(exc)
                if "confirm your age" in msg or "Sign in to confirm" in msg:
                    msg = ("YouTube wants a login for this one (age-restricted, common for GTA). "
                           "Run it again with --cookies chrome (or the browser you're signed into); in the cloud/phone use --cookies-file cookies.txt.")
                elif "403" in msg and "kick" in url:
                    msg = "Kick blocked the download. Run it again with --cookies chrome."
                log(f"{url}: {msg[:400]}", "err")

    print()
    total = sum(len(r["clips"]) for r in results)
    log(f"done: {total} clip(s) from {len(results)} stream(s)", "ok")
    for r in results:
        print(f"\n  {r['name']} · {r['title'][:60]}\n  {r['summary']}\n  {r['outdir']}")
        for c in r["clips"]:
            print(f"    [{c['score']}] {c['duration']:>3.0f}s @{hhmmss(c['stream_start'])}  {c['hook_text']}")
    print()


if __name__ == "__main__":
    main()
