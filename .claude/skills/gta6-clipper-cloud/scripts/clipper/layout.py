"""16:9 stream -> 9:16 clip. Picks one of three looks per clip:

  stream - a small face in a corner = a facecam over gameplay. Facecam on top,
           gameplay underneath. This is how stream clips look on Shorts/TikTok.
  face   - a big face (just chatting / IRL) = follow the face with a smooth crop.
  fit    - no face = the whole frame over a blurred copy of itself.

Returns an ffmpeg filtergraph from [0:v] to [vout] and the name of the look."""

import re
import threading
from pathlib import Path

import numpy as np

from .util import even, log

_quiet = threading.local()


def _say(msg):
    if not getattr(_quiet, "on", False):
        log(msg, "info")

STEP = 0.5            # seconds between sampled frames
MAX_SAMPLES = 60
SMALL_FACE = 0.085    # face narrower than 8.5% of the frame = a facecam overlay


MODEL = Path.home() / ".gta6-clipper" / "models" / "yunet.onnx"


def _faces(source, t0, t1, info):
    """[(t, x, y, w, h)] for every face in every sampled frame, in source pixels.
    Uses OpenCV's YuNet model (good on small, tilted, capped faces in a facecam);
    falls back to the older Haar detector if the model file is missing."""
    try:
        import cv2
    except ImportError as exc:
        log(f"  face detection unavailable ({exc})", "warn")
        return [], [], [], 1.0
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        return [], [], [], 1.0
    scale = 960.0 / info["width"] if info["width"] > 960 else 1.0
    size = (int(info["width"] * scale), int(info["height"] * scale))
    yunet = haar = None
    if MODEL.exists() and hasattr(cv2, "FaceDetectorYN"):
        yunet = cv2.FaceDetectorYN.create(str(MODEL), "", size, 0.6, 0.3, 5000)
    else:
        haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    step = max(STEP, (t1 - t0) / MAX_SAMPLES)
    found, sampled, grays, times, t = [], 0, [], [], t0
    try:
        while t < t1:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok:
                break
            sampled += 1
            small = cv2.resize(frame, size) if scale != 1.0 else frame
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            grays.append(gray)
            times.append(round(t - t0, 3))
            if yunet is not None:
                _, faces = yunet.detect(small)
                faces = [] if faces is None else [f[:4] for f in faces]
            else:
                faces = haar.detectMultiScale(cv2.equalizeHist(gray), 1.1, 5, minSize=(20, 20))
            for x, y, w, h in faces:
                found.append((round(t - t0, 3), x / scale, y / scale, w / scale, h / scale))
            t += step
    finally:
        cap.release()
    return found, times, grays, scale


def _cam_rect(grays, scale, box, info):
    """The facecam's own rectangle, found two ways:
    1. its border: straight lines that stay put while everything around them changes;
    2. its background: the still area around the face (a wall, a chair) that stops
       where the game or trailer starts changing.
    Falls back to a safe box ~5.5 face-widths wide, centred on the face."""
    fx, fy, fw, fh = [v * scale for v in box]
    sw, sh = info["width"] * scale, info["height"] * scale
    gw = 5.5 * fw
    guess = (fx + fw / 2 - gw / 2, fy + fh * 0.55 - gw * 0.35, gw, gw * 0.7)
    guess = [v / scale for v in guess]
    if len(grays) < 6:
        return guess, "guess"

    def plausible(x0, y0, x1, y1):
        w, h = x1 - x0, y1 - y0
        return (x0 <= fx and fx + fw <= x1 and y0 <= fy + fh * 0.3 and fy + fh * 0.8 <= y1
                and 2.2 * fw <= w <= 12 * fw and 1.8 * fh <= h <= 7.5 * fh
                and w <= 0.7 * sw and h <= 0.7 * sh)

    stack = np.stack([g.astype(np.int16) for g in grays])
    H, W = stack.shape[1:]

    # 1. border lines
    eh = (np.abs(np.diff(stack, axis=1)) > 10).mean(axis=0)
    ev = (np.abs(np.diff(stack, axis=2)) > 10).mean(axis=0)
    xs = slice(int(max(0, fx - 1.5 * fw)), int(min(W, fx + 2.5 * fw)))
    ys = slice(int(max(0, fy - 0.5 * fh)), int(min(H, fy + 1.5 * fh)))
    row = (eh[:, xs] > 0.5).mean(axis=1)
    col = (ev[ys, :] > 0.5).mean(axis=0)

    def border(scores, lo, hi, outward):
        lo, hi = int(max(0, lo)), int(min(len(scores) - 1, hi))
        idx = [i for i in range(lo, hi + 1) if scores[i] >= 0.55]
        if not idx:
            return None
        if outward == "low":
            outer = min(idx)
            return max(i for i in idx if i <= outer + 5) + 2
        outer = max(idx)
        return min(i for i in idx if i >= outer - 5) - 1

    y0 = border(row, fy - 3.5 * fh, fy - 0.2 * fh, "low")
    y1 = border(row, fy + 1.2 * fh, fy + 4.5 * fh, "high")
    x0 = border(col, fx - 7 * fw, fx - 0.2 * fw, "low")
    x1 = border(col, fx + 1.2 * fw, fx + 8 * fw, "high")
    y0 = y0 if y0 is not None else (0 if fy - 3.5 * fh <= 0 else None)
    y1 = y1 if y1 is not None else (sh if fy + 4.5 * fh >= sh else None)
    x0 = x0 if x0 is not None else (0 if fx - 7 * fw <= 0 else None)
    x1 = x1 if x1 is not None else (sw if fx + 8 * fw >= sw else None)
    if None not in (x0, x1, y0, y1) and plausible(x0, y0, x1, y1):
        return [x0 / scale, y0 / scale, (x1 - x0) / scale, (y1 - y0) / scale], "border"

    # 2. the still background around the face
    try:
        import cv2
        still = (stack.std(axis=0) < 9).astype(np.uint8)
        still = cv2.morphologyEx(still, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(still, connectivity=4)
        ring = labels[int(max(0, fy - 0.6 * fh)):int(min(H, fy + fh * 1.6)),
                      int(max(0, fx - 1.2 * fw)):int(min(W, fx + fw * 2.2))]
        ids, counts = np.unique(ring[ring > 0], return_counts=True)
        if len(ids):
            k = int(ids[np.argmax(counts)])
            x0, y0, w, h = stats[k][:4]
            if plausible(x0, y0, x0 + w, y0 + h):
                return [x0 / scale, y0 / scale, w / scale, h / scale], "still area"
    except Exception:
        pass
    return guess, "guess"


def _fit(cfg, info):
    W, H = cfg["output_width"], cfg["output_height"]
    pre = ""
    target = cfg.get("fit_aspect") or 0
    if target and info["width"] / info["height"] > target:
        cw = even(info["height"] * target)
        pre = f"crop={cw}:{even(info['height'])}:{even((info['width'] - cw) / 2)}:0,"
    return (f"[0:v]split=2[bg][fg];"
            f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"gblur=sigma=32,eq=brightness=-0.12[bgb];"
            f"[fg]{pre}scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,setsar=1[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,format=yuv420p[vout]")


def _stream(cfg, info, cam):
    """Facecam panel on top, gameplay panel below. `cam` = the facecam rectangle."""
    W, H = cfg["output_width"], cfg["output_height"]
    top = even(cfg["cam_height"])
    bot = H - top
    sw, sh = info["width"], info["height"]
    x, y, w, h = cam
    want = W / top                      # aspect of the top panel
    if w / h > want:                    # cam wider than the panel: trim its sides
        cw, ch = h * want, h
    else:                               # taller: trim top and bottom
        cw, ch = w, w / want
    cx = min(max(x + (w - cw) / 2, 0), sw - cw)
    cy = min(max(y + (h - ch) / 2, 0), sh - ch)
    gh = sh
    gw = min(sw, gh * W / bot)
    gx = (sw - gw) / 2
    if x > sw / 2:                      # cam on the right: keep the gameplay crop left of it
        gx = max(0, min(gx, x - gw))
    else:                               # cam on the left: keep it right of the cam
        gx = min(sw - gw, max(gx, x + w))
    return (f"[0:v]split=2[a][b];"
            f"[a]crop={even(cw)}:{even(ch)}:{even(cx)}:{even(cy)},"
            f"scale={W}:{top}:flags=lanczos,setsar=1[cam];"
            f"[b]crop={even(gw)}:{even(gh)}:{even(gx)}:0,scale={W}:{bot}:flags=lanczos,setsar=1[game];"
            f"[cam][game]vstack=inputs=2,format=yuv420p[vout]")


def _follow(cfg, info, faces):
    """Big face: a 9:16 crop that follows it smoothly."""
    sw, sh = info["width"], info["height"]
    cw = even(min(sw, sh * cfg["output_width"] / cfg["output_height"]))
    max_x = sw - cw
    xs = [x + w / 2 - cw / 2 for _, x, _, w, _ in faces]
    k = max(3, cfg["track_smoothing"] | 1)
    xs = np.convolve(np.pad(xs, (k // 2, k // 2), mode="edge"), np.ones(k) / k, mode="valid")
    pts = [(faces[0][0], xs[0])]
    for (t, *_), x in zip(faces[1:], xs[1:]):
        if abs(x - pts[-1][1]) >= 12 and t - pts[-1][0] >= 0.75:
            pts.append((t, x))
    expr = f"{min(max(pts[-1][1], 0), max_x):.1f}"
    for (t0, x0), (t1, x1) in reversed(list(zip(pts, pts[1:]))):
        a, b = min(max(x0, 0), max_x), min(max(x1, 0), max_x)
        expr = f"if(lt(t,{t1:.2f}),({a:.1f}+({b:.1f}-{a:.1f})*(t-{t0:.2f})/{max(t1 - t0, 0.01):.2f}),{expr})"
    return (f"[0:v]crop={cw}:{even(sh)}:x='{expr}':y=0,"
            f"scale={cfg['output_width']}:{cfg['output_height']}:flags=lanczos,setsar=1,format=yuv420p[vout]")


def _facecam(faces, sampled, info, grays=(), scale=1.0):
    """The facecam face: shows up in the same spot in most frames, is ALIVE (a face on
    a poster or a thumbnail never moves) and usually sits toward a side or corner."""
    cells = {}
    for t, x, y, w, h in faces:
        key = (int((x + w / 2) / info["width"] * 12), int((y + h / 2) / info["height"] * 8))
        cells.setdefault(key, []).append((t, x, y, w, h))
    stack = np.stack([g.astype(np.float32) for g in grays]) if len(grays) >= 4 else None
    best, best_score, best_frames = None, 0.0, 0
    seen, strong = set(), 0
    for key in sorted(cells, key=lambda k: -len(cells[k])):
        group = [f for k, v in cells.items() if abs(k[0] - key[0]) <= 1 and abs(k[1] - key[1]) <= 1 for f in v]
        sig = frozenset(id(f) for f in group)
        if sig in seen:
            continue
        seen.add(sig)
        frames = len({round(f[0], 2) for f in group})
        if frames >= 0.6 * max(1, sampled):
            strong += 1
        box = np.median(np.array([f[1:] for f in group]), axis=0)
        score = float(frames)
        if stack is not None:
            x, y, w, h = [int(v * scale) for v in box]
            patch = stack[:, max(0, y):y + max(1, h), max(0, x):x + max(1, w)]
            if patch.size and float(patch.std(axis=0).mean()) < 3.0:
                score *= 0.15                       # frozen face: a poster, not a person
        cx = (box[0] + box[2] / 2) / info["width"]
        if cx < 0.3 or cx > 0.7:
            score *= 1.25                           # cams sit toward the sides
        if score > best_score:
            best, best_score, best_frames = tuple(box), score, frames
    if best is None:
        return None, 0.0, strong
    return best, best_frames / max(1, sampled), strong


def _in_known(faces, times, known):
    """A cam rectangle learned from this stream's other clips that has a face in it
    for most of this stretch, or None."""
    for cam in reversed(known.get("cams", [])):
        x, y, w, h = cam
        hit = {f[0] for f in faces if x <= f[1] + f[3] / 2 <= x + w and y <= f[2] + f[4] / 2 <= y + h}
        if len(hit) >= 0.4 * max(1, len(times)):
            return cam
    return None


def _decide(faces, times, grays, scale, info, cfg, want, known):
    """The look for one stretch of a clip. Returns (filtergraph [0:v]->[vout], look)."""
    if want == "fit":
        return _fit(cfg, info), "fit"
    big = {f[0] for f in faces if f[3] / info["width"] >= SMALL_FACE}
    if want in ("auto", "face") and len(big) >= 0.5 * max(1, len(times)):
        per = {}
        for f in faces:
            if f[0] not in per or f[3] * f[4] > per[f[0]][3] * per[f[0]][4]:
                per[f[0]] = f
        return _follow(cfg, info, [per[k] for k in sorted(per)]), "face"
    box, share, strong = _facecam(faces, len(times), info, grays, scale)
    learned = _in_known(faces, times, known)
    if box is None or share < 0.3 or (strong > 2 and want == "auto"):
        if learned:
            _say("  unclear scene, but the streamer's cam is where it was in the other clips -> using it")
            return _stream(cfg, info, learned), "stream"
        if box is not None and share >= 0.3:
            _say(f"  {strong} faces on screen (thumbnails?) -> whole frame, to be safe")
        return _fit(cfg, info), "fit"
    small = box[2] / info["width"] < SMALL_FACE
    if want == "stream" or (want == "auto" and small):
        cam, how = _cam_rect(grays, scale, box, info)
        if how == "guess" and learned:
            cam, how = learned, "the stream's other clips"
        elif how != "guess":
            known.setdefault("cams", []).append(cam)
        _say(f"  facecam {int(cam[2])}x{int(cam[3])} at {int(cam[0])},{int(cam[1])} (found by {how})")
        return _stream(cfg, info, cam), "stream"
    per = {}
    for f in faces:
        if f[0] not in per or f[3] * f[4] > per[f[0]][3] * per[f[0]][4]:
            per[f[0]] = f
    return _follow(cfg, info, [per[k] for k in sorted(per)]), "face"


def _segments(faces, times, info, t_len, min_len=2.5):
    """Split a clip where the stream changes scene: small facecam over a game, a big
    full-screen cam, or no face at all. Returns [(start, end)] in clip time."""
    if len(times) < 4:
        return [(0.0, t_len)]
    W = info["width"]
    by_t = {}
    for f in faces:
        by_t.setdefault(f[0], []).append(f)
    states = []
    for t in times:
        fs = by_t.get(t, [])
        states.append("face" if any(f[3] / W >= SMALL_FACE for f in fs) else "cam" if fs else "none")
    # smooth: each sample takes the majority of its 5 neighbours
    smooth = []
    for i in range(len(states)):
        win = states[max(0, i - 2):i + 3]
        smooth.append(max(set(win), key=win.count))
    runs = []
    for i, st in enumerate(smooth):
        if runs and runs[-1][0] == st:
            runs[-1][2] = i
        else:
            runs.append([st, i, i])
    bounds = [0.0] + [(times[r[1]] + times[r[1] - 1]) / 2 for r in runs[1:]] + [t_len]
    segs = [[runs[i][0], bounds[i], bounds[i + 1]] for i in range(len(runs))]
    # fold stretches shorter than min_len into a neighbour
    changed = True
    while changed and len(segs) > 1:
        changed = False
        for i, (st, a, b) in enumerate(segs):
            if b - a < min_len:
                j = i - 1 if i > 0 else i + 1
                segs[j][1], segs[j][2] = min(segs[j][1], a), max(segs[j][2], b)
                segs.pop(i)
                changed = True
                break
    merged = []
    for st, a, b in segs:
        if merged and merged[-1][0] == st:
            merged[-1][2] = b
        else:
            merged.append([st, a, b])
    return [(a, b) for _, a, b in merged]


def _relabel(chain, i):
    chain = re.sub(r"\[([A-Za-z]+)\]", lambda m: f"[{m.group(1)}_{i}]", chain)
    return chain.replace("[0:v]", f"[in_{i}]")


def analyze(source, t0, t1, info):
    """Sample the clip's frames once (faces + greyscale frames); reused by graph()."""
    return _faces(source, t0, t1, info)


def graph(source, t0, t1, cfg, info, known=None, analysis=None, quiet=False):
    """(filtergraph [0:v]->[vout], look, top-panel height or None).
    A clip whose stream changes scene part-way is cut into parts, each framed its own
    way, then joined. `known` carries the facecam spots learned from the stream's other
    clips, so an unclear clip can still put the streamer on top."""
    _quiet.on = quiet
    try:
        known = known if known is not None else {}
        want = cfg["layout"]
        if want == "fit":
            return _fit(cfg, info), "fit", None
        faces, times, grays, scale = analysis or _faces(source, t0, t1, info)
        segs = _segments(faces, times, info, t1 - t0) if want == "auto" else [(0.0, t1 - t0)]
        parts = []
        for a, b in segs:
            idx = [i for i, t in enumerate(times) if a <= t < b]
            sub_f = [(round(f[0] - a, 3),) + tuple(f[1:]) for f in faces if a <= f[0] < b]
            sub_t = [round(times[i] - a, 3) for i in idx]
            sub_g = [grays[i] for i in idx]
            chain, look = _decide(sub_f, sub_t, sub_g, scale, info, cfg, want, known)
            parts.append((a, b, chain, look))
        stream_time = sum(b - a for a, b, _, look in parts if look == "stream")
        cam_h = even(cfg["cam_height"]) if stream_time >= 0.5 * (t1 - t0) else None
        looks = "+".join(dict.fromkeys(p[3] for p in parts))
        if len(parts) == 1:
            _say(f"  look: {looks}")
            return parts[0][2], looks, cam_h
        _say(f"  scene changes inside the clip -> {len(parts)} parts: "
             + ", ".join(f"{p[3]} {p[0]:.0f}-{p[1]:.0f}s" for p in parts))
        split = f"[0:v]split={len(parts)}" + "".join(f"[s_{i}]" for i in range(len(parts)))
        trims = ";".join(f"[s_{i}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[in_{i}]"
                         for i, (a, b, _, _) in enumerate(parts))
        bodies = ";".join(_relabel(chain, i) for i, (_, _, chain, _) in enumerate(parts))
        fix = ";".join(f"[vout_{i}]setsar=1,format=yuv420p[vs_{i}]" for i in range(len(parts)))
        join = (fix + ";" + "".join(f"[vs_{i}]" for i in range(len(parts)))
                + f"concat=n={len(parts)}:v=1:a=0[vout]")
        return f"{split};{trims};{bodies};{join}", looks, cam_h
    finally:
        _quiet.on = False
