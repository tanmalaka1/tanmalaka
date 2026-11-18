# GTA 6 Clipper: a free Claude Code skill

Give it GTA 6 streams, or let it find the biggest ones itself, and it gives you ready-to-post vertical clips:
the streamer's first reactions, crazy discoveries, insane reactions and funny moments, each with the facecam on
top, gameplay below, a hook title and word-by-word captions. Every clip comes with a caption and hashtags to paste.

- **It reads the whole stream for you.** It listens to every minute, marks where the streamer got loud and where
  chat went off, and Claude picks the moments worth posting.
- **Several streams at once.** `--find --streams 7` ≈ 21 clips.
- **$0 per clip.** Everything runs on your computer. Picking the moments uses your own Claude subscription.
  No API keys, no credits.
- **Small downloads.** Only the stream's audio plus the minutes that become clips (≈0.1 GB per stream hour).

## Install (phone / cloud)
This skill lives in the repo's `.claude/skills/`, so any Claude Code cloud session on this repo (start one from the
Claude app on your phone) loads it automatically. Then just say: *"clip this stream: <link>"* or
*"find today's biggest GTA 6 streams and clip them"*. The first run does setup itself. The session's environment
must allow the hosts listed in SKILL.md. Clips are pushed to the repo branch so you can download them on the phone.

## Good to know
- Age-restricted YouTube streams (common with GTA) and Kick need your browser login: add `--cookies chrome`.
- Your list of streamers to check is `~/gta6-clips/streamers.txt`. Put the streamers you clip for in it.
- Twitch's live top list needs your own free Twitch app key (steps in SKILL.md). Without it, YouTube search and
  your watchlist still work.
- Watch each clip before you post it. Clip streamers who are fine with clipping; most big ones run clipping
  programs and pay per view.

Layout: `SKILL.md` (what Claude follows) · `scripts/gta6clip` (the command) · `scripts/setup.sh` ·
`scripts/clipper/` (find, fetch, scan, moments, layout, render, captions, hook) · `streamers.txt` (default watchlist).
