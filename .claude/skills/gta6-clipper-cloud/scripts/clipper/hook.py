"""Render the static hook bar — the title card pinned over the top of the clip.

Drawn with Pillow rather than libass because libass cannot render Apple's colour
emoji (they come out as empty boxes), and because this gets us rounded corners.
"""

from PIL import Image, ImageDraw, ImageFont

# Apple Color Emoji ships a single 160px bitmap strike; any other size is rejected,
# so we always render at 160 and scale down.
import os
_EMOJI_CANDIDATES = (
    ("/System/Library/Fonts/Apple Color Emoji.ttc", 160),
    ("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", 109),
    ("/usr/share/fonts/noto/NotoColorEmoji.ttf", 109),
)
EMOJI_FONT, EMOJI_STRIKE = next(((p, s) for p, s in _EMOJI_CANDIDATES if os.path.exists(p)), (None, 160))
_BOLD_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
)


def _hook_font_path(cfg):
    path = cfg.get("hook_font_file")
    if path and path != "auto" and os.path.exists(path):
        return path
    return next(p for p in _BOLD_CANDIDATES if os.path.exists(p))

_EMOJI_RANGES = (
    (0x1F300, 0x1FAFF),   # pictographs, emoticons, symbols
    (0x2600, 0x27BF),     # misc symbols and dingbats
    (0x1F000, 0x1F2FF),   # tiles, enclosed characters
    (0xFE00, 0xFE0F),     # variation selectors
    (0x2B00, 0x2BFF),
)


def _is_emoji(ch):
    point = ord(ch)
    return any(lo <= point <= hi for lo, hi in _EMOJI_RANGES)


def _runs(word):
    """Split a word into consecutive (is_emoji, text) runs."""
    out = []
    for ch in word:
        kind = _is_emoji(ch)
        if out and out[-1][0] == kind:
            out[-1][1] += ch
        else:
            out.append([kind, ch])
    return [(kind, text) for kind, text in out]


def _emoji_image(ch, size):
    """One colour emoji, rendered at the native strike then scaled."""
    if not EMOJI_FONT:
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))
    font = ImageFont.truetype(EMOJI_FONT, EMOJI_STRIKE)
    tile = Image.new("RGBA", (EMOJI_STRIKE + 40, EMOJI_STRIKE + 40), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((0, 0), ch, font=font, embedded_color=True)
    return tile.crop(tile.getbbox() or (0, 0, 1, 1)).resize(
        (size, size), Image.LANCZOS
    )


def _measure(word, font, em):
    width = 0
    for is_emoji, text in _runs(word):
        width += len(text) * em if is_emoji else font.getlength(text)
    return width


def render(text, cfg, path):
    """Draw the hook bar to `path`. Returns (path, width, height)."""
    size = cfg["hook_font_size"]
    font = ImageFont.truetype(_hook_font_path(cfg), size)
    em = int(size * 1.05)                     # emoji sit slightly larger than caps
    pad_x, pad_y = int(size * 0.55), int(size * 0.34)
    line_h = int(size * 1.30)
    space = font.getlength(" ")
    max_text = cfg["output_width"] - 2 * cfg["hook_margin_x"] - 2 * pad_x

    # Greedy wrap; the hook is short so this is always 1-3 lines.
    lines, current = [], []
    for word in text.split():
        trial = current + [word]
        width = sum(_measure(w, font, em) for w in trial) + space * (len(trial) - 1)
        if current and width > max_text:
            lines.append(current)
            current = [word]
        else:
            current = trial
    if current:
        lines.append(current)

    widths = [
        sum(_measure(w, font, em) for w in ln) + space * (len(ln) - 1) for ln in lines
    ]
    box_w = int(max(widths) + 2 * pad_x)
    box_h = int(len(lines) * line_h + 2 * pad_y)

    img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (0, 0, box_w - 1, box_h - 1),
        radius=int(size * 0.30),
        fill=(255, 255, 255, 255),
    )

    for row, (line, width) in enumerate(zip(lines, widths)):
        x = (box_w - width) / 2                # centre each line, like the reference
        y = pad_y + row * line_h
        for word in line:
            for is_emoji, chunk in _runs(word):
                if is_emoji:
                    for ch in chunk:
                        try:
                            glyph = _emoji_image(ch, em)
                        except (OSError, ValueError):
                            continue           # unsupported glyph: skip, never draw tofu
                        img.alpha_composite(
                            glyph, (int(x), int(y + (line_h - em) / 2))
                        )
                        x += em
                else:
                    draw.text((x, y), chunk, font=font, fill=(0, 0, 0, 255))
                    x += font.getlength(chunk)
            x += space

    img.save(path)
    return path, box_w, box_h
