# XZ Tour Videos · 出口·我们 · 现场视频

Static page that plays fan-shot videos of Xiao Zhan's 「出口·我们」 tour in setlist order: pick a show, then
Prev / Next through the songs, with a 机位 (camera) switch when a song has several clips. Videos are only
**embedded or linked**, never re-hosted (they belong to the people who filmed them).

- **Live (2026-10-27):** https://xz-tour.pages.dev/ — public repo `canadaxfx/xz-tour` → Cloudflare Pages project `xz-tour`
  (connected to Git: every push to `main` deploys in ~30 s). Google Analytics GA4 `G-D462LSL414` (added 2026-10-27).
- **Publishing:** the admin's **发布 Publish (N)** button (red when data is waiting): `/api/publish` commits only the
  changed files under `data/` ("Publish from admin: …") and pushes with autosync's `github_token` as a one-off
  `http.extraheader` (never stored); it refuses if origin/main is ahead. Then the page polls `/api/publish/live`
  (compares the live JSON with the local file) and says 已上线 when Cloudflare serves it (~30–60 s). Code changes
  (`index.html`, `tools/`, …) are NOT published by the button — commit and push those by hand. `_redirects` keeps `CLAUDE.md`, `tools/`, `run_admin.*`,
  `.gitignore` off the site; `_backup/` and `*.tmp` are git-ignored.
- **Preview:** `.claude/launch.json` → `xz-tour-videos` (`python -m http.server 8888`), or the admin's `/site/`.

## Files

| Path | What |
|------|------|
| `index.html` | The whole site, CSS + JS inline. Reads `data/tour.json`, then the chosen show file. |
| `data/tour.json` | The list of shows. `file: null` = greyed out ("整理中"). **One entry per city (2026-10-26):** a city's shows with the same setlist are merged — `"dates": [...]`, `"label": "10.23 + 10.25"` (shown as "南京站 10.23 + 10.25"), `"aliases"` = old ids (`nanjing-1`, `nanjing-2`) so old links still open it. `"date"` stays the first night (admin's "posted before the show" check); the "posted after the next stop" check uses the last of `dates`. |
| `data/shows/<id>.json` | Setlist + clips. Song `n` is "1".."23", encores "E1"/"E2"; optional `note`, `cover` (原唱), `encore`, **talks** (2026-10-26: spoken parts are their own entries,
`{"n": "T16", "talk": true, "title": "谈下一个十年", "clips": []}`, placed right after their song; videos are optional — without
one it shows as the old grey note and Prev/Next skip it, with one it's a lighter clickable line, "讲话" above the player;
song numbers / "N 首" / "16 / 25" count songs only; added/renamed/deleted with the admin's ＋讲话 / 改名 / 删 (`/api/talk`,
delete only while empty; song guessing ignores talks). **Combined entries** (2026-10-27) for one video covering two
songs back to back: `{"n": "E1+E2", "combo": ["E1", "E2"], "title": "最幸运的幸运 + 满足", "clips": []}` (+ `encore` if
both are), placed after the second song (and its talks). The admin's ＋合并 (in 编辑歌单 mode only, since 2026-10-26) on a song combines it with the next song
(`/api/talk` op `combo`; rename/delete like talks). The site hides empty ones, shows "E1+E2" as the number, leaves them
out of the song count, and Prev/Next include them. Song guessing picks the combined entry when a post names both songs.
Links encode the "+" (`song=E1%2BE2`). The old `after` field was converted; pre-change copy
`_backup/shows/nanjing.pretalks.json`), `only` (a date: the song was performed on that night only → "仅 10.25"). Clips carry **no** date (the user's choice: both nights' videos are simply mixed). Nanjing = `nanjing.json`; the pre-merge `nanjing-1.json` is in `_backup/shows/`. Both Nanjing setlists were identical (checked against a full 10.25 list). |
| `tools/admin_server.py`, `tools/admin.html` | Local admin (below). |
| `run_admin.vbs` / `run_admin.bat` | Start the admin on port **5041** and open it. (5040 is held by Windows.) |
| `img/stage-exit-1600.jpg`, `img/stage-exit-800.jpg` | Page background (desktop / phone): 肖战工作室's red doorway stage photo, R2 `full/20261025_演唱会_XZ-STU-19.jpg` (post `weibo.com/6643123988/Roiqpi7YD`), resized, metadata stripped. Credited in the footer. |
| `_backup/shows/` | Automatic copy of the show file before every admin save. |

**Design (2026-10-26), after the tour's key visuals:** 肖战's announcement post `weibo.com/1792951112/5353499090223628`
(R2 `full/20261013_XZ-Weibo-1.jpg` / `-2.jpg`) and the stage photos above. **No green and no yellow anywhere** (the user's
rule; the old 安全出口 green theme was replaced). Tokens on `:root`: warm black `--night #0B0708`, panels `#170D0F`, tour red
`--red #D8102A` (current song, selected 机位, open dropdown, logo chevrons), `--red-text #FF5A68` for small red text,
`--red-deep #9E0E1E` (corner band), rose links `#FFB3B9`. Logo = the key visual's "HERE / COMES / OUR" stacked in heavy
type with the EXIT drawn as red ‹ › chevrons (HTML + inline SVG, `.logo`). Background = the doorway photo fading to black
(`body::before`) + a red diagonal corner band (`body::after`). Phones hide the English part of the title (the logo says it).
Stage photos with gold/yellow light (STU-22, 29, 31, 36) were deliberately not used.
The admin page (`tools/admin.html`) uses the same tokens, plus `--ok #FFB3B9` (rose: saved), `--warn #C3A6FF` (lilac:
missing length, no video, hidden clip, date warnings — yellow before) and `--bad #FF5A68` (errors).

**Douyin player size (2026-10-25):** the official embed picks its layout from the frame width. From **768 px** it's a
wide player that fills the frame, so on desktop (player column ≈790 px) it gets the normal 16:9 box, unscaled. Below
768 px it uses a fixed phone layout (672 px player + 48 px bar = 720 px, whatever the frame height; ~750 px is a
half-way state with a sideways scrollbar), which a 16:9 box crops. Only then `fitDouyin()` (ResizeObserver on `.player`)
adds `.dyfit`: a 1:2 box with the frame kept at 360×720 and scaled into it. Its height is set in JS to fill the screen
below the box's top edge (page at the top, 10 px margin), capped at 720 px and 2× the column width, never under 60% of the
screen (the CSS `min(64vh, …)` is only a fallback). Phones (≤600 px) also get a one-line header to leave it room.
Measured: 360×640 → 241×481 · 375×812 → 327×653 · 430×932 → 360×720 (full size); the 机位 buttons start just below.
Phone held sideways (wider than tall and ≤500 px tall) adds `.dyland` instead: the box is the full column width and
the screen height minus 16 px, and the frame is at least 800 px wide (so Douyin loads its wide layout) scaled down to the
column. Douyin picks its layout when it loads, so `fitDouyin()` (also on window `resize`) reloads the player only when
it needs the other layout (`data-layout` phone/wide), e.g. on rotation. The wide layout is always ~35 px taller than its
frame, so small inner scrollbars remain there (desktop too). Bilibili/YouTube always keep the 16:9 box. The 机位 buttons wrap (no sideways scroll)
so every version is visible, and `.grid` is `minmax(0, 1fr)` so the phone layout can't grow wider than the screen.

**Setlist on phones (one column, <920 px):** the song line above the player ("16 / 25 余年 · 歌单 ▾", `#now`) opens the
setlist as a custom dropdown — the same `.setlist` element as the desktop sidebar (talk segments, 安可, 待补充, 机位
counts, green current song), `position: fixed` under the song line (`openList()`), scrolled to the current song. Picking a
song, tapping outside, Esc or scrolling the page closes it. There is no separate list at the bottom on phones. Desktop
(≥920 px) keeps the sidebar and the song line isn't clickable. A native `<select>` was rejected: plain one-line entries.

**Length:** `duration` (whole seconds) is saved when a clip is added, shown on the site's 机位 buttons + credit line and
next to each clip in the admin (`?:??` in yellow = missing). The admin's 补时长 button (`/api/fill-durations`) fills
missing ones: Bilibili via `x/player/pagelist` (not 412-blocked), Weibo via the post's `media_info.duration` (matched
by `objectId`). Douyin/YouTube can't be looked up — type them in 改 (m:ss).

**R2 clips (2026-10-26):** `platform: "r2"`, `url` (an `assets.xz-studio-gallery.com/….mp4` file, or any direct
.mp4/.mov/.m4v/.webm link), optional `source` (the original post). The site plays them in a plain `<video>` (`.vertical`
box if portrait), labels them 本站, and the credit links "在微博打开原帖" when `source` is set. Admin: paste the R2 link →
HEAD check (must be 200 + a video), `ffprobe` over the network for length and orientation (rotation flag handled), and
autosync's gallery JSON (`Nextcloud/AI Tools/XZ-autosync/gallery-json`, main PC only) for the original post — a file can
be in several galleries, the record with a `link` wins. The filmer comes from that Weibo post (`media_info.author_name`,
e.g. 新华社/央视新闻 reposted by 肖战工作室), the song from 《…》 in the post text, gallery text or file name. Files over
150 MB or above 1440p get a "may buffer on phones" warning (the site plays the file as is). 改 has a 原帖 field for R2
clips; 补时长 uses ffprobe for them.

**Version order (2026-10-27):** the site never uses the file order. `siteOrder()` (same code in `index.html` and
`tools/admin.html`) sorts a song's visible clips: **官摄** (note contains 官摄 = official footage, e.g. 新华社/央视新闻) always first,
then pinned (`"top": true`, admin **顶** toggle, shown as **推荐**), then clips whose note contains 全景, then the rest —
longest first within each group (pinned keep their admin order); clips without a length go last in their group.
The admin lists clips in that order (hidden ones last) and has no ↑↓ any more (`/api/move` is unused); `&clip=N` links
count in that order. On the site the versions are one line "‹ 推荐 机位 1/6 · by · platform · note · len ▾ ›" opening a
list (`renderVersions()`), hidden when a song has only one version.

**Clip fields:** `platform` (`bilibili` + `bvid` [+ `p` for a multi-part video] · `douyin` + `vid` · `youtube` + `id` [+ `start`]
· `weibo` + `bid`, `mid`, `objectId`), then `by` (filmer, required), `via` (re-uploader), `note`, `vertical` (9:16).
The first clip of a song is the one that plays by default. Weibo has no embed, so it shows as a 在微博打开 card.

## Admin (添加视频)

Paste a link or a whole share text → it reads the video → you check the song / filmer → Save writes the show file.
- **Bilibili:** title, uploader, upload time, and every part of a multi-part video as its own row. The song is guessed
  from the title/part name and the filmer from "cr xxx" in a part name.
- **Weibo:** `by` = the video's owner (`media_info.author_name`), `via` = the account that posted it, if different.
  A post with several videos (`mix_media_info`) has no per-video titles, so each row gets "第N个视频" and you pick the song.
- **Douyin:** only the id is read (details need signed APIs). The admin shows the official embed so you can read the
  author and type it in. `note`/`slides` posts are refused because they can't be embedded.
- **YouTube:** oEmbed. **RedNote:** refused for now.
- **Checks:** a clip already in the show is greyed out; saving needs a song and a filmer; there's a warning when the post
  date is before the show or on/after the next show's date (show 1 and 2 clips are easy to mix up).
- **Saving:** backup to `_backup/shows/`, write to a `.tmp`, check it parses, then replace. The writer keeps the
  one-line-per-clip layout.
- **Each clip in the right-hand setlist:** ↑ ↓ reorder within the song (the first clip, outlined green, is the one the
  site plays by default) · 改 edit filmer / via / note / 竖屏, or move it to another song (it goes to the end there)
  · 藏/显 hide/show (sets `"hidden": true`: kept in the file as a backup, filtered out by `index.html` on load, shown
  dimmed + crossed out in the admin; "plays first" and the 看 links follow the visible clips only)
  · 看 open on the site · 删 delete. Clips are matched by their video id (`clip_key`), so editing the credit is safe.
- **Restart after code changes:** the page's 重启 Restart button (`/api/restart`) starts a fresh, windowless copy of
  `admin_server.py --wait-port` (it waits until 5041 is free) and exits the old one; the page polls `/api/ping` until the
  PID changes, then reloads. Needed because `run_admin.bat` reuses a server already on 5041. `admin.html` itself is read
  from disk on every load, so page-only changes need just a browser reload.
- **＋新增一站 (2026-10-27):** city, English name (→ id and file `shows/<id>.json`), dates ("11.08 11.09", this tour's
  year), venue, setlist source; the setlist is either **copied** from an existing stop (songs, covers, notes, talks — no
  videos, no combined entries, no `only`) or **pasted** text parsed by `parse_setlist()` (《title》 lines; 原唱：X / -X,
  肖战 left empty; 安可 on the line or as a heading; short lines between songs = talk candidates you can untick; `#…`,
  `*…`, header lines and `cr …` credits skipped) with a preview before creating. Adds the stop to `tour.json` (backed up
  to `_backup/shows/tour.<time>.json`, sorted by date). A new stop shows on the site at once, even with no videos.
- **编辑歌单 mode:** a stop-info form (city, English name, dates, venue, source; also updates `tour.json`) and per song
  ✎ (title, **编号**, 原唱, note, 仅 one date, 安可) · ↑↓ (moves the song with its talks) · ＋上/＋下 (insert) · 删 (only without
  videos and not part of a combined entry). Video rows are hidden in this mode. Every change saves at once (`/api/setlist`)
  and `renumber()` renumbers songs 1…/E1…, talks T<song>, combined entries from their songs — shared song links may then
  point elsewhere, so settle a setlist early.
- **编号 (custom number, 2026-10-26):** a song entry with `"num"` (e.g. "★", "E1+E2") shows that mark instead of a number,
  is skipped by automatic numbering and the "N 首" count (`n` = the mark, made unique for links). Plain numbers like "3"
  or "E2" are refused (they'd clash with automatic ones). Empty = automatic again. Nanjing's "E1+E2+退场" video entry is ★.

Platform details behind the resolvers: `Platform API Reference/` (Weibo §19, Bilibili and Douyin notes in
`MEDIA_EXTRACTION_TROUBLESHOOTING.md`).
