"""Settings. Defaults below; override any of them in ~/gta6-clips/config.json."""

import json
from pathlib import Path

HOME = Path.home() / "gta6-clips"

DEFAULTS = {
    # --- finding streams ---
    "game": "Grand Theft Auto VI",   # Twitch category name (official API only)
    "search": "GTA 6",               # YouTube search words
    "title_words": ["gta"],          # a watchlist VOD must have one of these in its title
    "max_age_hours": 36,             # ignore watchlist VODs older than this

    # --- download ---
    "max_height": 1080,

    # --- listening to the stream ---
    "scan_model": "tiny",            # fast pass over the whole stream (what Claude reads)
    "caption_model": "base",         # careful pass over each chosen clip (the captions)
    "language": None,                # None = detect, or "en"
    "scan_block_minutes": 20,        # audio is decoded in blocks, so 10-hour streams fit in memory
    "loud_db": 6.0,                  # louder than the last 2 minutes by this much = a spike
    "chat": True,                    # YouTube replays: count chat messages per 5 s
    "chat_timeout": 420,

    # --- picking moments ---
    "focus": "gta6",                 # gta6 | general (any stream: IRL, travel, other games)
    "clips_per_stream": 3,
    "min_duration": 15,
    "max_duration": 60,
    "chunk_minutes": 12,             # transcript window per Claude call
    "claude_parallel": 4,            # Claude calls at the same time (more = faster, more usage)
    "claude_model": None,            # None = your Claude Code default, or "sonnet" / "opus"

    # --- framing ---
    "layout": "auto",                # auto | stream | face | fit
    "output_width": 1080,
    "output_height": 1920,
    "cam_height": 760,               # stream layout: height of the facecam panel on top
    "fit_aspect": 1.7778,            # fit layout: 1.7778 keeps the whole 16:9 frame (cams sit on edges)
    "track_smoothing": 5,

    # --- hook (the title bar) ---
    "hook_font_file": "auto",       # auto = Arial Bold on Mac, else Liberation/DejaVu Bold
    "hook_font_size": 54,
    "hook_y": 0.09,
    "hook_margin_x": 150,

    # --- captions ---
    "font": "Arial Black",           # Linux: libass falls back to a system bold font; try "Liberation Sans" if thin
    "font_size": 82,
    "words_per_group": 4,
    "uppercase": True,
    "text_color": "FFFFFF",
    "highlight_color": "FFE13B",
    "outline_width": 6,
    "caption_y": 0.72,
    "word_pop": True,

    # --- encoding ---
    "encoder": "libx264",            # or h264_videotoolbox (Mac hardware, much faster)
    "crf": 20,
    "preset": "medium",
    "audio_bitrate": "192k",
    "loudnorm": True,
}


def load(overrides=None):
    cfg = dict(DEFAULTS)
    path = HOME / "config.json"
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text()))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path} is not valid JSON: {exc}")
    for key, value in (overrides or {}).items():
        if value is not None:
            cfg[key] = value
    return cfg
