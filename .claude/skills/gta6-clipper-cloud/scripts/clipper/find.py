"""Find the GTA 6 streams worth clipping right now, biggest first.

Sources (all free):
  1. YouTube: today's most-viewed long GTA 6 streams (public search, no key).
  2. Your watchlist (~/gta6-clips/streamers.txt): each streamer's newest VOD
     with GTA in the title, on Twitch or YouTube.
  3. Twitch top live GTA 6 streams, only if you add your own free Twitch API key
     (TWITCH_CLIENT_ID + TWITCH_CLIENT_SECRET in ~/gta6-clips/.env).
"""

import json
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import HOME
from .fetch import base_args
from .util import log, run

# YouTube search filter: sort by view count + uploaded today + longer than 20 min.
YT_TODAY_LONG = "CAMSBAgCGAI%253D"


def _flat(url, limit, args=()):
    try:
        raw = run(["yt-dlp", "--flat-playlist", "--playlist-end", str(limit), "-J", *args, url],
                  timeout=120)
        return json.loads(raw).get("entries") or []
    except (RuntimeError, ValueError) as exc:
        log(f"could not read {url[:60]}: {str(exc).splitlines()[0][:80]}", "warn")
        return []


def youtube_today(query, limit=15):
    url = ("https://www.youtube.com/results?search_query="
           f"{urllib.parse.quote_plus(query)}&sp={YT_TODAY_LONG}")
    out = []
    for e in _flat(url, limit, base_args("youtube")):
        if e.get("_type") not in (None, "url") or not e.get("id"):
            continue
        out.append({
            "url": f"https://www.youtube.com/watch?v={e['id']}", "platform": "youtube",
            "name": e.get("channel") or "?", "title": e.get("title") or "",
            "views": e.get("view_count") or 0, "duration": e.get("duration") or 0,
            "live": e.get("live_status") == "is_live", "source": "youtube search",
        })
    return out


def watchlist_file():
    path = HOME / "streamers.txt"
    if not path.exists():
        HOME.mkdir(parents=True, exist_ok=True)
        default = Path(__file__).resolve().parent.parent.parent / "streamers.txt"
        path.write_text(default.read_text() if default.exists() else "")
    return path


def _watch_entries():
    out = []
    for line in watchlist_file().read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        platform, _, handle = line.partition(":")
        if platform in ("twitch", "youtube", "kick") and handle:
            out.append((platform, handle.strip()))
    return out


def _latest(platform, handle, cfg):
    """Newest VOD of one streamer, if it's recent and about GTA."""
    if platform == "twitch":
        listing = f"https://www.twitch.tv/{handle}/videos?filter=archives&sort=time"
    elif platform == "youtube":
        listing = f"https://www.youtube.com/{handle if handle.startswith('@') else '@' + handle}/streams"
    else:
        return None   # Kick needs browser cookies; paste Kick links by hand
    args = base_args(listing)
    entries = _flat(listing, 3, args)
    words = [w.lower() for w in cfg["title_words"]]
    for e in entries:
        title = (e.get("title") or "")
        if words and not any(w in title.lower() for w in words):
            continue
        url = e.get("url") or e.get("webpage_url")
        if url and not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={e.get('id')}"
        try:
            full = json.loads(run(["yt-dlp", "-J", "--no-playlist", *args, url], timeout=120))
        except (RuntimeError, ValueError):
            continue
        ts = full.get("timestamp") or full.get("release_timestamp") or 0
        if ts and time.time() - ts > cfg["max_age_hours"] * 3600:
            return None
        return {
            "url": url, "platform": platform, "name": full.get("channel") or full.get("uploader") or handle,
            "title": title, "views": full.get("view_count") or 0, "duration": full.get("duration") or 0,
            "live": full.get("live_status") == "is_live", "source": "watchlist",
        }
    return None


def watchlist(cfg):
    entries = _watch_entries()
    if not entries:
        return []
    log(f"checking {len(entries)} streamer(s) on your watchlist...", "step")
    with ThreadPoolExecutor(max_workers=6) as pool:
        got = list(pool.map(lambda e: _latest(e[0], e[1], cfg), entries))
    return [g for g in got if g]


def _env():
    vals = dict(os.environ)
    env_file = HOME / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() and v.strip():
                vals.setdefault(k.strip(), v.strip().strip('"'))
    return vals


def twitch_top(cfg, limit=10):
    """Official Twitch API (Helix) with the member's own free app key. Optional."""
    env = _env()
    cid, secret = env.get("TWITCH_CLIENT_ID"), env.get("TWITCH_CLIENT_SECRET")
    if not (cid and secret):
        return []

    def call(url, method="GET", headers=None):
        req = urllib.request.Request(url, method=method, headers=headers or {})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())

    try:
        token = call("https://id.twitch.tv/oauth2/token?" + urllib.parse.urlencode(
            {"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"}),
            method="POST")["access_token"]
        h = {"Client-Id": cid, "Authorization": f"Bearer {token}"}
        games = call("https://api.twitch.tv/helix/games?" + urllib.parse.urlencode({"name": cfg["game"]}), headers=h)
        if not games.get("data"):
            log(f"Twitch has no category called '{cfg['game']}'", "warn")
            return []
        streams = call("https://api.twitch.tv/helix/streams?" + urllib.parse.urlencode(
            {"game_id": games["data"][0]["id"], "first": limit}), headers=h)["data"]
        out = []
        for s in streams:
            vids = call("https://api.twitch.tv/helix/videos?" + urllib.parse.urlencode(
                {"user_id": s["user_id"], "type": "archive", "first": 1}), headers=h)["data"]
            if not vids:
                continue          # this streamer doesn't save VODs; nothing to clip
            out.append({
                "url": vids[0]["url"], "platform": "twitch", "name": s["user_name"],
                "title": s["title"], "views": s["viewer_count"], "duration": 0,
                "live": True, "source": "twitch top live (viewers now)",
            })
        return out
    except Exception as exc:  # network / key problems shouldn't stop the run
        log(f"Twitch API skipped: {exc}", "warn")
        return []


def find(cfg, limit=5):
    """Biggest streams first, one per streamer."""
    found = twitch_top(cfg) + watchlist(cfg) + youtube_today(cfg["search"] + " stream")
    best = {}
    for s in found:
        if not any(w.lower() in s["title"].lower() for w in cfg["title_words"]):
            continue
        key = s["name"].lower()
        if key not in best or s["views"] > best[key]["views"]:
            best[key] = s
    ranked = sorted(best.values(), key=lambda s: -s["views"])
    return ranked[:limit]
