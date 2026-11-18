"""Stage 5 — build word-by-word animated captions as an ASS subtitle file.

Renders a few words at a time with the currently-spoken word highlighted, which is the
style that performs on TikTok/Reels/Shorts.
"""

# Pauses shorter than this keep the previous phrase on screen instead of blinking off.
# Longer silences do clear the caption, which reads as intentional rather than broken.
HOLD_THROUGH_PAUSE = 1.2

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, \
BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, \
BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Pop,{font},{size},{primary},{primary},&H00000000,&H96000000,-1,0,0,0,100,100,0,0,\
1,{outline},3,2,70,70,{marginv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_color(rrggbb):
    """RRGGBB (human) -> &HBBGGRR& (ASS byte order)."""
    value = rrggbb.lstrip("#")
    return f"&H00{value[4:6]}{value[2:4]}{value[0:2]}"


def _timestamp(seconds):
    seconds = max(0.0, seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{int(hours):d}:{int(minutes):02d}:{secs:05.2f}"


def _escape(text):
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def _group(words, cfg):
    """Chunk words into caption-sized phrases, breaking on pauses and sentence ends."""
    groups, current = [], []
    for index, word in enumerate(words):
        current.append(word)
        text = word["w"].rstrip()
        gap_next = (words[index + 1]["s"] - word["e"]) if index + 1 < len(words) else 99

        if (
            len(current) >= cfg["words_per_group"]
            or text.endswith((".", "!", "?"))
            or (text.endswith(",") and len(current) >= 3)
            or gap_next > 0.55
        ):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def build(words, start, end, path, cfg):
    """Write an ASS file covering [start, end); timings are rebased to the clip."""
    primary = _ass_color(cfg["text_color"])
    highlight = _ass_color(cfg["highlight_color"])
    marginv = int(cfg["output_height"] * (1.0 - cfg["caption_y"]))

    lines = [HEADER.format(
        w=cfg["output_width"], h=cfg["output_height"],
        font=cfg["font"], size=cfg["font_size"],
        primary=primary, outline=cfg["outline_width"], marginv=marginv,
    )]


    inside = [w for w in words if w["e"] > start and w["s"] < end]
    groups = _group(inside, cfg)

    for gi, group in enumerate(groups):
        texts = [_escape(w["w"].strip()) for w in group]
        if cfg["uppercase"]:
            texts = [t.upper() for t in texts]

        # Where the next phrase takes over — never render past it, or the two overlap.
        next_start = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else end
        tail_limit = min(next_start, end)

        for index, word in enumerate(group):
            # Hold each word until the next begins so captions never flicker off.
            ev_start = max(word["s"], start) - start
            if index + 1 < len(group):
                ev_end = max(group[index + 1]["s"], word["e"]) - start
            elif tail_limit - word["e"] <= HOLD_THROUGH_PAUSE:
                # Short breath between phrases: hold the caption so it doesn't blink out.
                ev_end = tail_limit - start
            else:
                ev_end = min(word["e"] + 0.3, tail_limit) - start
            if ev_end <= ev_start:
                ev_end = ev_start + 0.08

            pop = r"\fscx112\fscy112" if cfg["word_pop"] else ""
            unpop = r"\fscx100\fscy100" if cfg["word_pop"] else ""

            rendered = []
            for j, text in enumerate(texts):
                if j == index:
                    rendered.append(f"{{\\c{highlight}&{pop}}}{text}{{\\c{primary}&{unpop}}}")
                else:
                    rendered.append(text)

            lines.append(
                f"Dialogue: 0,{_timestamp(ev_start)},{_timestamp(ev_end)},Pop,,0,0,0,,"
                + " ".join(rendered)
            )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
