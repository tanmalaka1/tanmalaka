#!/usr/bin/env bash
# Download only the selected clips from Lacy's GTA 6 stream (YouTube VOD).
# Requires: yt-dlp and ffmpeg installed. Each clip has ~3s buffer before/after.
# Run all:        bash download_clips.sh
# Run one clip:   bash download_clips.sh 01

URL="https://www.youtube.com/live/mMtyL4AGIrk"
OUT="clips"
mkdir -p "$OUT"

dl() {
  # $1=id  $2=name  $3=start  $4=end
  if [ -n "$ONLY" ] && [ "$ONLY" != "$1" ]; then return; fi
  yt-dlp -f "bv*[height<=1080]+ba/b" \
    --download-sections "*$3-$4" --force-keyframes-at-cuts \
    --merge-output-format mp4 \
    -o "$OUT/$1_$2.%(ext)s" "$URL"
}

ONLY="$1"

# ---- Top 15 ----
dl 01 darla_saw_the_clip           10:23:07 10:24:08
dl 02 dog_abuser_tiktok            10:04:24 10:05:58
dl 03 darla_make_space_for_lucia   00:08:06 00:09:49
dl 04 alligator_8_hours            08:26:30 08:28:43
dl 04b alligator_punchline         08:30:21 08:31:03
dl 05 not_a_dog_abuser             10:13:22 10:15:34
dl 06 giveaway_winner_lost         00:29:50 00:32:00
dl 07 best_gta_player              09:57:02 09:59:00
dl 08 honest_review_mid            04:19:31 04:20:38
dl 09 gta6_unlocks                 00:44:27 00:45:53
dl 10 npc_predator                 03:30:45 03:32:46
dl 11 dog_kick                     01:59:03 02:00:54
dl 12 safe_was_a_fridge            03:40:26 03:43:23
dl 13 drake_radio                  01:40:17 01:41:43
dl 14 drew_is_the_goat             11:02:12 11:04:02
dl 15 rip_donnie                   10:38:12 10:39:44

# ---- Cadangan ----
dl b1 closet_story                 00:06:23 00:07:28
dl b2 darla_camera_roll            06:29:59 06:32:39
dl b3 13_years_construction        05:30:34 05:32:14
dl b4 erie_pennsylvania            10:51:28 10:52:49
dl b5 stop_glazing_me              08:02:17 08:04:07
dl b6 hitting_animals              06:49:16 06:52:53
