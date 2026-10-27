"""XZ Tour Videos admin: paste a video link, pick the song, save the clip into data/shows/<show>.json.

Run: run_admin.vbs (hidden) or run_admin.bat (console). Opens http://localhost:5041/.
The site itself is served at /site/ so a saved clip can be checked right away.

Link resolving (verified Oct 2026, see Platform API Reference/):
- Bilibili: x/web-interface/view returns 412 to scripts, the WBI-signed /wbi/view works (needs a buvid3 cookie).
- Weibo: a guest SUB cookie from genvisitor2 is enough for weibo.com/ajax/statuses/show.
  media_info.author_name is the video's owner (the filmer), user.screen_name is whoever posted/reposted it.
- Douyin: post details need signed app/web APIs (not rebuilt here, see MEDIA_EXTRACTION_TROUBLESHOOTING.md §6),
  so only the id is read; the official embed's auth call tells whether it can be embedded. Names are typed by hand.
"""
import hashlib
import json
import re
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from flask import Flask, abort, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent.parent          # the site folder
DATA = ROOT / "data"
BACKUP = ROOT / "_backup" / "shows"
PORT = 5041                                            # 5040 is held by Windows (svchost, Connected Devices Platform)
CN = timezone(timedelta(hours=8))                      # show dates are China dates

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
SHOW_ID = re.compile(r"^[a-z0-9-]{1,40}$")

app = Flask(__name__)


# ---------------------------------------------------------------- show files

def tour():
    return json.loads((DATA / "tour.json").read_text(encoding="utf-8"))


def show_path(show_id):
    if not SHOW_ID.match(show_id or ""):
        abort(400, "bad show id")
    meta = next((s for s in tour()["shows"] if s["id"] == show_id and s.get("file")), None)
    if not meta:
        abort(404, "show has no setlist file yet")
    return DATA / meta["file"]


def load_show(show_id):
    return json.loads(show_path(show_id).read_text(encoding="utf-8"))


def dump_show(show):
    """Same layout as the hand-written files: one line per song header, one line per clip."""
    j = lambda v: json.dumps(v, ensure_ascii=False)
    lines = ["{"]
    keys = [k for k in show if k != "songs"]
    for k in keys:
        lines.append(f"  {j(k)}: {j(show[k])},")
    lines.append('  "songs": [')
    for si, song in enumerate(show["songs"]):
        head = {k: v for k, v in song.items() if k != "clips"}
        head_txt = j(head)[:-1]                       # drop the closing brace, clips follow
        clips = song.get("clips", [])
        end = "," if si < len(show["songs"]) - 1 else ""
        if not clips:
            lines.append(f'    {head_txt}, "clips": []}}{end}')
            continue
        lines.append(f'    {head_txt}, "clips": [')
        for ci, c in enumerate(clips):
            lines.append(f"      {j(c)}{',' if ci < len(clips) - 1 else ''}")
        lines.append(f"    ]}}{end}")
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def save_show(show_id, show):
    path = show_path(show_id)
    BACKUP.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, BACKUP / f"{path.stem}.{datetime.now():%Y%m%d-%H%M%S}.json")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(dump_show(show), encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))       # never replace the file with something unreadable
    tmp.replace(path)


def clip_key(c):
    p = c.get("platform")
    if p == "bilibili":
        return f"bilibili:{c.get('bvid')}:{c.get('p') or 1}"
    if p == "douyin":
        return f"douyin:{c.get('vid')}"
    if p == "youtube":
        return f"youtube:{c.get('id')}"
    if p == "weibo":                                   # one post can hold several videos
        return f"weibo:{c.get('mid') or c.get('bid')}:{c.get('objectId') or ''}"
    return f"{p}:{c.get('url')}"


# ---------------------------------------------------------------- helpers

def first_url(text):
    m = re.search(r"https?://[^\s，。、“”\"'<>）)]+", text or "")
    return m.group(0) if m else (text or "").strip()


def follow(url, ua=UA):
    """Expand a short link (b23.tv, v.douyin.com, t.cn, mapp.api.weibo.cn …) without downloading pages."""
    r = requests.get(url, headers={"User-Agent": ua}, allow_redirects=True, timeout=15, stream=True)
    r.close()
    return r.url


def cn_time(ts):
    return datetime.fromtimestamp(ts, CN).strftime("%Y-%m-%d %H:%M") if ts else ""


def guess_note(text):
    notes = [w for w in ("全景", "直拍", "4K", "横屏", "竖屏", "清唱", "不完整") if w.lower() in (text or "").lower()]
    return " ".join(notes)


def guess_by(part):
    """Compilation part names look like '4《克卜勒》-孙燕姿 cr碱基一圈（全景）'."""
    m = re.search(r"cr[\s.．:：]*([^\s（(【\[]+)", part or "", re.I)
    return m.group(1) if m else ""


def guess_song(text, show):
    """Song whose title appears in the text. A title written as 《…》 wins (so "［出口·我们］…唱的《轻轻》" is 轻轻,
    not 我们); otherwise the longest title found (so 《光点》 beats a stray 光)."""
    text = (text or "").lower()
    by_n = {s["n"]: s for s in show["songs"]}
    for c in show["songs"]:                         # a combined entry (E1+E2) when every one of its songs is named
        if c.get("combo") and all(by_n.get(n) and by_n[n]["title"].lower() in text for n in c["combo"]):
            return c["n"]
    songs = [s for s in show["songs"] if s["title"] and not s.get("talk") and not s.get("combo")]   # talks are picked by hand
    quoted = [s for s in songs if f"《{s['title'].lower()}》" in text]
    hits = quoted or [s for s in songs if s["title"].lower() in text]
    if not hits:
        return None
    return max(hits, key=lambda s: len(s["title"]))["n"]


# ---------------------------------------------------------------- Bilibili

MIXIN = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14,
         39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6,
         63, 57, 62, 11, 36, 20, 34, 44, 52]
_bili = {"session": None, "mixin": None, "at": 0}


def bili_session():
    if _bili["session"] is None or time.time() - _bili["at"] > 3600:   # WBI keys rotate daily; refresh hourly
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
        spi = s.get("https://api.bilibili.com/x/frontend/finger/spi", timeout=15).json()["data"]
        s.cookies.set("buvid3", spi["b_3"], domain=".bilibili.com")
        s.cookies.set("buvid4", spi["b_4"], domain=".bilibili.com")
        img = s.get("https://api.bilibili.com/x/web-interface/nav", timeout=15).json()["data"]["wbi_img"]
        key = lambda u: u.rsplit("/", 1)[1].split(".")[0]
        raw = key(img["img_url"]) + key(img["sub_url"])
        _bili.update(session=s, mixin="".join(raw[i] for i in MIXIN)[:32], at=time.time())
    return _bili["session"]


def bili_view(params):
    s = bili_session()
    p = dict(params, wts=int(time.time()))
    q = urllib.parse.urlencode(sorted(p.items()))
    p["w_rid"] = hashlib.md5((q + _bili["mixin"]).encode()).hexdigest()
    j = s.get("https://api.bilibili.com/x/web-interface/wbi/view", params=p, timeout=15).json()
    if j.get("code") != 0:
        raise ValueError(f"B站返回错误 {j.get('code')}: {j.get('message')}")
    return j["data"]


def resolve_bilibili(url, show):
    if "b23.tv" in url:
        url = follow(url)
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    a = re.search(r"/av(\d+)", url)
    if not (m or a):
        raise ValueError("没找到 BV 号 · No BV id in the link")
    d = bili_view({"bvid": m.group(1)} if m else {"aid": a.group(1)})
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    want_p = int(q["p"][0]) if q.get("p", [""])[0].isdigit() else None
    owner = d["owner"]["name"]
    pages = []
    for pg in d.get("pages", []):
        text = pg["part"] if len(d["pages"]) > 1 else d["title"]
        pages.append({
            "p": pg["page"], "part": pg["part"], "duration": pg["duration"],
            "song": guess_song(text, show),
            "by": (guess_by(pg["part"]) if len(d["pages"]) > 1 else "") or owner,
            "note": guess_note(text),
        })
    multi = len(pages) > 1
    base = {"platform": "bilibili", "bvid": d["bvid"]}
    return {
        "platform": "bilibili",
        "title": d["title"], "uploader": owner, "published": cn_time(d.get("pubdate")),
        "duration": d.get("duration"), "desc": (d.get("desc") or "")[:400],
        "embeddable": True,
        "multi": multi, "focusP": want_p,
        "items": [
            {"clip": dict(base, **({"p": pg["p"]} if multi else {})),
             "label": f"P{pg['p']} {pg['part']}" if multi else d["title"],
             "duration": pg["duration"], "song": pg["song"] or (guess_song(d["title"], show) if not multi else None),
             "by": pg["by"], "via": owner if multi and pg["by"] != owner else "", "note": pg["note"]}
            for pg in pages
        ],
    }


# ---------------------------------------------------------------- Weibo

_weibo = {"session": None, "at": 0}


def weibo_session():
    if _weibo["session"] is None or time.time() - _weibo["at"] > 3600:
        s = requests.Session()
        s.headers["User-Agent"] = UA
        s.post("https://passport.weibo.com/visitor/genvisitor2",
               data={"cb": "visitor_gray_callback", "tid": "", "from": "weibo"}, timeout=15)
        _weibo.update(session=s, at=time.time())
    return _weibo["session"]


def resolve_weibo(url, show):
    s = weibo_session()
    if urllib.parse.urlparse(url).netloc in ("t.cn", "mapp.api.weibo.cn", "share.api.weibo.cn"):   # app share links
        url = follow(url)
    oid = re.search(r"(1034:\d+)", urllib.parse.unquote(url))
    post_id = None
    if oid and ("/tv/show/" in url or "video.weibo.com" in url or "h5.video.weibo" in url):
        r = s.post("https://weibo.com/tv/api/component", params={"page": "/tv/show/" + oid.group(1)},
                   data={"data": json.dumps({"Component_Play_Playinfo": {"oid": oid.group(1)}})},
                   headers={"Referer": "https://weibo.com/tv/show/" + oid.group(1)}, timeout=15)
        post_id = str(r.json()["data"]["Component_Play_Playinfo"]["mid"])
    else:
        path = urllib.parse.urlparse(url).path.strip("/").split("/")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        post_id = (q.get("id") or q.get("mid") or [None])[0] or (path[-1] if path else None)
    if not post_id or not re.match(r"^[0-9A-Za-z]{8,20}$", post_id):
        raise ValueError("没找到微博 id · No Weibo post id in the link")
    r = s.get("https://weibo.com/ajax/statuses/show", params={"id": post_id},
              headers={"Referer": "https://weibo.com/"}, timeout=15, allow_redirects=False)
    if r.status_code != 200:
        raise ValueError(f"微博返回 {r.status_code}（帖子可能已删除或需要登录）")
    d = r.json()
    if d.get("ok") == 0 or "idstr" not in d:
        raise ValueError("微博找不到这条帖子 · Post not found: " + str(d.get("message") or ""))
    # One video sits in page_info; a post with several videos lists them in mix_media_info (no per-video titles).
    pi = d.get("page_info") or {}
    videos = [pi] if pi.get("object_type") == "video" else [
        x["data"] for x in (d.get("mix_media_info") or {}).get("items", [])
        if x.get("type") == "video" and (x.get("data") or {}).get("object_id")]
    if not videos:
        raise ValueError("这条微博没有视频 · This post has no video")
    text = d.get("text_raw") or ""
    if d.get("isLongText"):
        try:
            text = s.get("https://weibo.com/ajax/statuses/longtext", params={"id": d["mblogid"]}, timeout=15
                         ).json()["data"]["longTextContent"]
        except Exception:
            pass
    poster = d["user"]["screen_name"]
    created = datetime.strptime(d["created_at"], "%a %b %d %H:%M:%S %z %Y").astimezone(CN)
    multi = len(videos) > 1
    items = []
    for i, v in enumerate(videos, 1):
        mi = v.get("media_info") or {}
        owner = mi.get("author_name") or poster
        clip = {"platform": "weibo", "bid": d["mblogid"], "mid": d["idstr"], "objectId": v.get("object_id")}
        items.append({"clip": clip, "label": f"视频 {i}" if multi else text[:80], "duration": mi.get("duration"),
                      "song": None if multi else guess_song(text, show),
                      "by": owner, "via": poster if poster != owner else "",
                      "note": f"第{i}个视频" if multi else guess_note(text)})
    return {
        "platform": "weibo",
        "title": text[:300], "uploader": poster, "published": created.strftime("%Y-%m-%d %H:%M"),
        "duration": None if multi else items[0]["duration"], "desc": "",
        "embeddable": False,
        "multi": multi,
        "manual": "这条微博有多个视频，没有分别的标题：请对照正文的歌名顺序逐个选歌 · Several videos, no titles: pick each song" if multi else None,
        "items": items,
    }


# ---------------------------------------------------------------- Douyin / YouTube

def resolve_douyin(url, show):
    if "v.douyin.com" in url:
        url = follow(url, ua="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148")
    if "/note/" in url or "/slides/" in url:
        raise ValueError("这是图文/日常帖，不能嵌入 · Photo/日常 posts can't be embedded")
    # A profile-page link carries two ids: modal_id = the video that was opened, vid = another one. Prefer modal_id.
    m = next(filter(None, (re.search(pat + r"(\d{15,21})", url) for pat in (r"modal_id=", r"/video/", r"aweme_id=", r"[?&]vid="))), None)
    if not m:
        raise ValueError("没找到抖音视频 id · No Douyin video id in the link")
    vid = m.group(1)
    ok = None
    try:
        r = requests.post("https://open.douyin.com/api/douyin/v1/open/auth/embed_video/", json={"item_id": vid},
                          headers={"User-Agent": UA, "Referer": f"https://open.douyin.com/player/video?vid={vid}"}, timeout=15)
        ok = bool(r.json().get("auth_result"))
    except Exception:
        pass
    return {
        "platform": "douyin",
        "title": "", "uploader": "", "published": "", "duration": None, "desc": "",
        "embeddable": ok,
        "multi": False,
        "manual": "抖音的标题和作者要看预览手动填 · Douyin: read the author from the preview and type it in",
        "items": [{"clip": {"platform": "douyin", "vid": vid}, "label": f"抖音 {vid}", "duration": None,
                   "song": None, "by": "", "via": "", "note": ""}],
    }


def resolve_youtube(url, show):
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})", url)
    if not m:
        raise ValueError("没找到 YouTube id · No YouTube id in the link")
    vid = m.group(1)
    j = requests.get("https://www.youtube.com/oembed", params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
                     headers={"User-Agent": UA}, timeout=15)
    info = j.json() if j.ok else {}
    t = re.search(r"[?&]t=(\d+)", url)
    clip = {"platform": "youtube", "id": vid, **({"start": int(t.group(1))} if t else {})}
    return {
        "platform": "youtube",
        "title": info.get("title", ""), "uploader": info.get("author_name", ""), "published": "",
        "duration": None, "desc": "", "embeddable": j.ok, "multi": False,
        "items": [{"clip": clip, "label": info.get("title", vid), "duration": None,
                   "song": guess_song(info.get("title", ""), show), "by": info.get("author_name", ""),
                   "via": "", "note": guess_note(info.get("title", ""))}],
    }


# ---------------------------------------------------------------- R2 (our own CDN)

R2_HOST = "assets.xz-studio-gallery.com"
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")
GALLERY_JSON = Path.home() / "Nextcloud" / "AI Tools" / "XZ-autosync" / "gallery-json"   # autosync's data (main PC)


def probe_video(url):
    """Length and frame size with ffprobe, read over the network (only the headers are fetched)."""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-v", "error", "-user_agent", UA, "-select_streams", "v:0",
                            "-show_entries", "stream=width,height:stream_side_data=rotation:format=duration", "-of", "json", url],
                           capture_output=True, text=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        j = json.loads(r.stdout or "{}")
        st = (j.get("streams") or [{}])[0]
        w, h = st.get("width"), st.get("height")
        rot = next((abs(int(x.get("rotation", 0))) for x in st.get("side_data_list", []) if "rotation" in x), 0)
        if rot == 90 and w and h:                   # phone videos stored sideways with a rotation flag
            w, h = h, w
        dur = float((j.get("format") or {}).get("duration") or 0)
        return {"duration": round(dur) if dur else None, "width": w, "height": h}
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def gallery_entry(url):
    """autosync's gallery record for this R2 file, if any (gives the original post link and its text)."""
    if not GALLERY_JSON.is_dir():
        return None
    name = urllib.parse.unquote(urllib.parse.urlparse(url).path)
    found = None                                    # one file can be in several galleries; prefer a record with the post link
    for f in GALLERY_JSON.glob("*/*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items = d if isinstance(d, list) else next((v for v in d.values() if isinstance(v, list)), [])
        for it in items:
            if isinstance(it, dict) and any(isinstance(v, str) and urllib.parse.unquote(urllib.parse.urlparse(v).path) == name
                                            for v in (it.get("url"), it.get("videoUrl"), it.get("video"))):
                if it.get("link"):
                    return it
                found = found or it
    return found


def resolve_r2(url, show):
    r = requests.head(url, headers={"User-Agent": UA}, timeout=15, allow_redirects=True)
    if r.status_code != 200:
        raise ValueError(f"R2 文件打不开（{r.status_code}）· File not found on R2")
    if not (r.headers.get("Content-Type", "").startswith("video/") or url.lower().endswith(VIDEO_EXT)):
        raise ValueError("这不是视频文件 · Not a video file")
    info = probe_video(url) or {}
    g = gallery_entry(url) or {}
    source, text, by = g.get("link") or "", g.get("postText") or "", ""
    m = re.search(r"weibo\.com/\d+/([0-9A-Za-z]+)", source)
    if m:                                           # who posted it: the original Weibo post's account
        try:
            d = weibo_session().get("https://weibo.com/ajax/statuses/show", params={"id": m.group(1)},
                                    headers={"Referer": "https://weibo.com/"}, timeout=15).json()
            by = ((d.get("page_info") or {}).get("media_info") or {}).get("author_name") or d["user"]["screen_name"]
            text = d.get("text_raw") or text
        except (requests.RequestException, ValueError, KeyError):
            pass
    w, h = info.get("width"), info.get("height")
    size_mb = round(int(r.headers.get("Content-Length", 0)) / 1048576)
    clip = {"platform": "r2", "url": url, **({"source": source} if source else {})}
    warnings = []
    if size_mb > 150 or (w and h and min(w, h) > 1440):
        warnings.append(f"文件很大（{size_mb} MB，{w}×{h}），手机上可能要缓冲 · Large file, may buffer on phones")
    name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
    return {
        "platform": "r2",
        "title": text[:300] or name, "uploader": by,
        "published": g.get("date", ""), "duration": info.get("duration"), "desc": "",
        "embeddable": True, "multi": False,
        "manual": None if by else "请填写拍摄者 · Fill in who filmed it",
        "extraWarnings": warnings,
        "items": [{"clip": clip, "label": name, "duration": info.get("duration"),
                   "vertical": bool(w and h and h > w),
                   "song": guess_song(" ".join([text, g.get("postText") or "", name]), show),
                   "by": by, "via": "", "note": guess_note(text)}],
    }


def resolve(raw, show):
    url = first_url(raw)
    host = urllib.parse.urlparse(url).netloc.lower()
    if not host:
        raise ValueError("请粘贴一个链接 · Paste a link")
    if "bilibili.com" in host or "b23.tv" in host:
        out = resolve_bilibili(url, show)
    elif "douyin.com" in host:
        out = resolve_douyin(url, show)
    elif "weibo." in host or host in ("t.cn",):
        out = resolve_weibo(url, show)
    elif "youtube.com" in host or "youtu.be" in host:
        out = resolve_youtube(url, show)
    elif host == R2_HOST or urllib.parse.urlparse(url).path.lower().endswith(VIDEO_EXT):
        out = resolve_r2(url, show)
    elif "xiaohongshu.com" in host or "xhslink.com" in host:
        raise ValueError("小红书暂不支持（不能嵌入，链接还会过期）· RedNote isn't supported yet")
    else:
        raise ValueError("不认识这个网站 · Unknown site: " + host)

    # Mark clips that are already in the show, and warn if the post is older than the show.
    have = {clip_key(c): s["n"] for s in show["songs"] for c in s["clips"]}
    for it in out["items"]:
        it["already"] = have.get(clip_key(it["clip"]))
    out["warnings"] = out.pop("extraWarnings", None) or []
    posted = (out.get("published") or "")[:10]
    last = max(show.get("dates") or [show["date"]])           # a merged city entry spans several nights
    later = sorted(s["date"] for s in tour()["shows"] if s["date"] > last)
    if posted and posted < show["date"]:
        out["warnings"].append(f"发布时间 {posted} 早于演出日期 {show['date']}，可能不是这一场 · Posted before the show date")
    elif posted and later and posted >= later[0]:
        out["warnings"].append(f"发布时间 {posted} 已在下一场（{later[0]}）当天或之后，请确认是这一站 · Posted on/after the next stop's date — check which stop it is")
    if out.get("embeddable") is False and out["platform"] == "douyin":
        out["warnings"].append("抖音说这个视频不允许嵌入 · Douyin says this video can't be embedded")
    return out


# ---------------------------------------------------------------- routes

@app.get("/")
def admin_page():
    return send_from_directory(Path(__file__).parent, "admin.html")


@app.get("/site/")
@app.get("/site/<path:p>")
def site(p="index.html"):
    return send_from_directory(ROOT, p, max_age=0)


@app.get("/api/tour")
def api_tour():
    return jsonify(tour())


@app.get("/api/show/<show_id>")
def api_show(show_id):
    return jsonify(load_show(show_id))


@app.post("/api/resolve")
def api_resolve():
    body = request.get_json(force=True)
    try:
        return jsonify(ok=True, **resolve(body.get("url", ""), load_show(body.get("show"))))
    except ValueError as e:
        return jsonify(ok=False, error=str(e))
    except requests.RequestException as e:
        return jsonify(ok=False, error=f"网络错误 · Network error: {e}")
    except (KeyError, TypeError, json.JSONDecodeError) as e:
        return jsonify(ok=False, error=f"平台返回的数据看不懂（可能改版了）· Unexpected platform response: {e!r}")


CLIP_FIELDS = {"platform", "bvid", "p", "vid", "id", "start", "bid", "mid", "objectId", "url", "source", "by", "via", "note", "vertical", "hidden", "duration", "top"}


@app.post("/api/add")
def api_add():
    body = request.get_json(force=True)
    show_id = body.get("show")
    show = load_show(show_id)
    by_n = {s["n"]: s for s in show["songs"]}
    have = {clip_key(c) for s in show["songs"] for c in s["clips"]}
    added, skipped = [], []
    for it in body.get("items", []):
        song = by_n.get(it.get("n"))
        clip = {k: v for k, v in (it.get("clip") or {}).items() if k in CLIP_FIELDS and v not in ("", None)}
        if clip.get("duration"):
            clip["duration"] = int(round(float(clip["duration"])))
        if not song or not clip.get("platform") or not clip.get("by"):
            skipped.append({"n": it.get("n"), "why": "缺少歌曲或拍摄者 · song and filmer are required"})
            continue
        if clip_key(clip) in have:
            skipped.append({"n": song["n"], "why": "已经有了 · already in this show"})
            continue
        # Readable key order in the file: ids first, then credit.
        order = ["platform", "bvid", "p", "vid", "id", "start", "bid", "mid", "objectId", "url", "source", "duration", "vertical", "by", "via", "note", "hidden", "top"]
        song["clips"].append({k: clip[k] for k in order if k in clip})
        have.add(clip_key(clip))
        added.append(song["n"])
    if added:
        save_show(show_id, show)
    return jsonify(ok=True, added=added, skipped=skipped, show=show)


@app.post("/api/delete")
def api_delete():
    body = request.get_json(force=True)
    show_id = body.get("show")
    show = load_show(show_id)
    song = next((s for s in show["songs"] if s["n"] == body.get("n")), None)
    if not song:
        abort(400, "no such song")
    key = clip_key(body.get("clip") or {})
    before = len(song["clips"])
    song["clips"] = [c for c in song["clips"] if clip_key(c) != key]
    if len(song["clips"]) == before:
        abort(400, "clip not found (file changed?) — reload")
    save_show(show_id, show)
    return jsonify(ok=True, show=show)


@app.post("/api/fill-durations")
def api_fill_durations():
    """Look up the length of every clip that doesn't have one yet. Bilibili and Weibo only:
    Douyin needs signed requests (type it in 改 instead), YouTube's oEmbed has no length."""
    body = request.get_json(force=True)
    show_id = body.get("show")
    show = load_show(show_id)
    bili_pages, weibo_posts = {}, {}
    filled, failed, manual = 0, [], []
    for song in show["songs"]:
        for c in song["clips"]:
            if c.get("duration"):
                continue
            label = f"{song['n']} {song['title']} · {c.get('by', '')}"
            try:
                if c["platform"] == "bilibili":
                    if c["bvid"] not in bili_pages:       # x/player/pagelist isn't behind the 412 wall
                        j = requests.get("https://api.bilibili.com/x/player/pagelist", params={"bvid": c["bvid"]},
                                         headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"}, timeout=15).json()
                        bili_pages[c["bvid"]] = {pg["page"]: pg["duration"] for pg in j.get("data") or []}
                    d = bili_pages[c["bvid"]].get(c.get("p") or 1)
                elif c["platform"] == "weibo":
                    if c["mid"] not in weibo_posts:
                        weibo_posts[c["mid"]] = weibo_session().get(
                            "https://weibo.com/ajax/statuses/show", params={"id": c["mid"]},
                            headers={"Referer": "https://weibo.com/"}, timeout=15).json()
                    post = weibo_posts[c["mid"]]
                    vids = [post.get("page_info") or {}] + [x.get("data") or {} for x in (post.get("mix_media_info") or {}).get("items", [])]
                    d = next(((v.get("media_info") or {}).get("duration") for v in vids if v.get("object_id") == c.get("objectId")), None)
                elif c["platform"] == "r2":
                    d = (probe_video(c["url"]) or {}).get("duration")
                else:
                    manual.append(label)
                    continue
            except (requests.RequestException, ValueError, KeyError, TypeError):
                d = None
            if d:
                c["duration"] = int(round(float(d)))
                filled += 1
            else:
                failed.append(label)
    if filled:
        # Same key order as added/edited clips (duration right after the ids); any unknown keys are kept, last.
        order = ["platform", "bvid", "p", "vid", "id", "start", "bid", "mid", "objectId", "url", "source", "duration", "vertical", "by", "via", "note", "hidden", "top"]
        for song in show["songs"]:
            song["clips"] = [{**{k: c[k] for k in order if k in c}, **{k: v for k, v in c.items() if k not in order}}
                             for c in song["clips"]]
        save_show(show_id, show)
    return jsonify(ok=True, show=show, filled=filled, failed=failed, manual=manual)


def find_clip(show, n, clip):
    """The song and index of a clip, matched by its video id (the credit fields may be what's being edited)."""
    song = next((s for s in show["songs"] if s["n"] == n), None)
    if not song:
        abort(400, "no such song")
    key = clip_key(clip or {})
    i = next((i for i, c in enumerate(song["clips"]) if clip_key(c) == key), None)
    if i is None:
        abort(400, "clip not found (file changed?) — reload")
    return song, i


EDITABLE = ("by", "via", "note", "vertical", "hidden", "duration", "source", "top")   # top: pinned to the top of the site's list   # hidden: kept as a backup, not shown on the site


@app.post("/api/update")
def api_update():
    """Edit a clip's credit/note, and optionally move it to another song (it goes to the end of that song)."""
    body = request.get_json(force=True)
    show_id = body.get("show")
    show = load_show(show_id)
    song, i = find_clip(show, body.get("n"), body.get("clip"))
    c = dict(song["clips"][i])
    for k in EDITABLE:
        if k not in (body.get("changes") or {}):
            continue
        v = body["changes"][k]
        v = v.strip() if isinstance(v, str) else v
        if k == "duration" and v not in ("", None):
            try:
                v = int(round(float(v)))                   # whole seconds; 0 removes it
            except (TypeError, ValueError):
                abort(400, "duration must be seconds")
        if v in ("", None, False):
            c.pop(k, None)
        else:
            c[k] = v
    if not c.get("by"):
        abort(400, "filmer (by) is required")
    order = ["platform", "bvid", "p", "vid", "id", "start", "bid", "mid", "objectId", "url", "source", "duration", "vertical", "by", "via", "note", "hidden", "top"]
    c = {k: c[k] for k in order if k in c}
    to_n = body.get("to") or song["n"]
    if to_n == song["n"]:
        song["clips"][i] = c
    else:
        target = next((s for s in show["songs"] if s["n"] == to_n), None)
        if not target:
            abort(400, "no such target song")
        if any(clip_key(x) == clip_key(c) for x in target["clips"]):
            abort(400, "that song already has this clip")
        del song["clips"][i]
        target["clips"].append(c)
    save_show(show_id, show)
    return jsonify(ok=True, show=show, n=to_n)


@app.post("/api/talk")
def api_talk():
    """Talks are setlist entries {"n": "T16", "talk": true, "title": …, "clips": []} placed after a song.
    op "add" (after: a song's n, title) · "rename" (n, title) · "delete" (n; only while it has no clips).
    op "combo" (from: a song's n): one entry for a video that covers that song and the next one, e.g.
    {"n": "E1+E2", "combo": ["E1", "E2"], "title": "最幸运的幸运 + 满足"}, placed after the second song.
    rename/delete also work on combined entries."""
    body = request.get_json(force=True)
    show_id = body.get("show")
    show = load_show(show_id)
    songs, op = show["songs"], body.get("op")
    title = (body.get("title") or "").strip()
    if op == "add":
        i = next((k for k, s in enumerate(songs) if s["n"] == body.get("after")), None)
        if i is None or not title:
            abort(400, "need an existing song and a title")
        j = i + 1
        while j < len(songs) and songs[j].get("talk"):   # after any talks that already follow that song
            j += 1
        taken = {s["n"] for s in songs}
        base = "T" + songs[i]["n"]
        n = base if base not in taken else next(f"{base}-{k}" for k in range(2, 99) if f"{base}-{k}" not in taken)
        songs.insert(j, {"n": n, "talk": True, "title": title, "clips": []})
    elif op == "combo":
        plain = [k for k, s in enumerate(songs) if not s.get("talk") and not s.get("combo")]
        i = next((k for k in plain if songs[k]["n"] == body.get("from")), None)
        nxt = [k for k in plain if i is not None and k > i]
        if i is None or not nxt:
            abort(400, "need a song that has a next song")
        a, b = songs[i], songs[nxt[0]]
        n = f"{a['n']}+{b['n']}"
        if any(s["n"] == n for s in songs):
            abort(400, f"{n} already exists")
        j = nxt[0] + 1
        while j < len(songs) and (songs[j].get("talk") or songs[j].get("combo")):   # after the second song's talks
            j += 1
        entry = {"n": n, "combo": [a["n"], b["n"]], "title": title or f"{a['title']} + {b['title']}"}
        if a.get("encore") and b.get("encore"):
            entry["encore"] = True
        songs.insert(j, {**entry, "clips": []})
    else:
        s = next((s for s in songs if s["n"] == body.get("n") and (s.get("talk") or s.get("combo"))), None)
        if not s:
            abort(400, "no such talk or combined entry")
        if op == "rename" and title:
            s["title"] = title
        elif op == "delete":
            if s["clips"]:
                abort(400, "move or delete its videos first")
            songs.remove(s)
        else:
            abort(400, "bad op")
    save_show(show_id, show)
    return jsonify(ok=True, show=show)


@app.post("/api/move")
def api_move():
    """Move a clip up (-1) or down (+1) within its song. The first clip is the one the site plays by default."""
    body = request.get_json(force=True)
    show_id = body.get("show")
    show = load_show(show_id)
    song, i = find_clip(show, body.get("n"), body.get("clip"))
    j = i + (1 if body.get("dir") == 1 else -1)
    if not 0 <= j < len(song["clips"]):
        return jsonify(ok=True, show=show)          # already at the top/bottom: nothing to save
    song["clips"][i], song["clips"][j] = song["clips"][j], song["clips"][i]
    save_show(show_id, show)
    return jsonify(ok=True, show=show)


# ---------------------------------------------------------------- setlists: new stop, parse, edit

def is_plain(s):
    """A real song (not a talk, not a combined entry)."""
    return not s.get("talk") and not s.get("combo")


def renumber(show):
    """Songs 1, 2, 3… in order, encores E1, E2…; talks become T<song before>; combined entries follow their songs.
    Clips live inside their entry, so renumbering never moves a video. Old song links (?song=5) may change meaning."""
    songs = show["songs"]
    mapping, k, e = {}, 0, 0
    for s in songs:                                 # automatic numbers first (entries with their own "num" don't count)
        if is_plain(s) and not s.get("num"):
            if s.get("encore"):
                e += 1; new = f"E{e}"
            else:
                k += 1; new = str(k)
            mapping[s["n"]] = new
            s["n"] = new
    taken = {s["n"] for s in songs if is_plain(s) and not s.get("num")}
    for s in songs:                                 # a custom number (★, E1+E2…) is shown as is; made unique for links
        if is_plain(s) and s.get("num"):
            n = s["num"] if s["num"] not in taken else next(f"{s['num']}{x}" for x in range(2, 99) if f"{s['num']}{x}" not in taken)
            mapping[s["n"]] = n; s["n"] = n; taken.add(n)
    last = "0"
    for s in songs:
        if is_plain(s):
            last = s["n"]
        elif s.get("talk"):
            base = "T" + last
            n = base if base not in taken else next(f"{base}-{x}" for x in range(2, 99) if f"{base}-{x}" not in taken)
            s["n"] = n; taken.add(n)
        elif s.get("combo"):
            s["combo"] = [mapping.get(x, x) for x in s["combo"]]
            s["n"] = "+".join(s["combo"]); taken.add(s["n"])
    return mapping


def blocks(songs):
    """Group entries: each song plus the talks / combined entries that follow it (moved together)."""
    out = []
    for s in songs:
        if is_plain(s) or not out:
            out.append([s])
        else:
            out[-1].append(s)
    return out


SETLIST_SKIP = re.compile(r"^(#|\*|＊|cr[.\s：:]|歌单|整理|合集|抢不到|\d{6,8}|.*演唱会.*第.场|.*巡回演唱会)", re.I)


def parse_setlist(text):
    """Setlist text from a Weibo post → entries. Song lines carry 《title》; "原唱：X" / "-X" is the original singer
    (肖战 = his own song, left empty); 安可 on the line or as a heading marks encores; short lines between songs
    become talk candidates (the admin lets you untick them)."""
    entries, encore = [], False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.search(r"《([^》]+)》", line)
        if not m:
            if re.fullmatch(r"[\s（(]*安可[\s）):：]*", line):
                encore = True
                continue
            if entries and not SETLIST_SKIP.match(line) and len(line) <= 30 and "@" not in line and "http" not in line:
                entries.append({"talk": True, "title": line.strip("—-－ ")})
            continue
        title = m.group(1).strip()
        rest = line[m.end():]
        enc = encore or "安可" in line
        cover = ""
        c = re.search(r"原唱[：:]\s*([^\s（(，,]+)", rest) or re.match(r"\s*[-－—]\s*([^\s（(，,]+)", rest)
        if c:
            cover = c.group(1)
            rest = rest.replace(c.group(0), " ")
        if cover in ("肖战", "Xiao Zhan"):
            cover = ""
        note = re.sub(r"[（(]?\s*安可\s*[）)]?|[（）()\[\]【】]", " ", rest)
        note = re.sub(r"cr.*$", "", note, flags=re.I).strip(" ，,。.·")    # "cr 某某" = who filmed it, not a song note
        song = {"title": title}
        if cover:
            song["cover"] = cover
        if note and len(note) <= 12:
            song["note"] = note
        if enc:
            song["encore"] = True
        entries.append(song)
    while entries and entries[-1].get("talk"):         # a trailing line after the last song is not a talk
        entries.pop()
    return entries


def parse_dates(text):
    """'2026-11-08, 2026-11-09' or '11.08 11.09' (this tour's year) → ['2026-11-08', …]"""
    year = (tour()["shows"] or [{"date": f"{datetime.now():%Y}-01-01"}])[0]["date"][:4]
    out = []
    for a in re.findall(r"(?:(\d{4})[-./年])?(\d{1,2})[-./月](\d{1,2})", text or ""):
        out.append(f"{a[0] or year}-{int(a[1]):02d}-{int(a[2]):02d}")
    return sorted(set(out))


def stop_label(dates):
    return " + ".join(d[5:].replace("-", ".") for d in dates)


def save_tour(t):
    p = DATA / "tour.json"
    BACKUP.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, BACKUP / f"tour.{datetime.now():%Y%m%d-%H%M%S}.json")
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(t, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(p)


@app.post("/api/setlist/parse")
def api_setlist_parse():
    return jsonify(ok=True, entries=parse_setlist(request.get_json(force=True).get("text", "")))


@app.post("/api/show/new")
def api_show_new():
    """New stop: city, cityEn (→ id), dates, venue, optional setlist source link, and either copyFrom (an existing
    stop's setlist: songs, covers, notes, talks — no videos, no combined entries) or entries (from the parser)."""
    b = request.get_json(force=True)
    city, city_en = (b.get("city") or "").strip(), (b.get("cityEn") or "").strip()
    dates = parse_dates(b.get("dates"))
    sid = re.sub(r"[^a-z0-9]+", "-", city_en.lower()).strip("-")
    t = tour()
    if not city or not sid or not dates:
        abort(400, "城市、英文名和日期都要填 · city, English name and dates are required")
    if any(s["id"] == sid or sid in s.get("aliases", []) for s in t["shows"]):
        abort(400, f"{sid} 已经存在 · already exists")
    if b.get("copyFrom"):
        src = load_show(b["copyFrom"])
        entries = [{k: v for k, v in s.items() if k not in ("clips", "only")} for s in src["songs"] if not s.get("combo")]
    else:
        entries = [{k: v for k, v in e.items() if k in ("title", "cover", "note", "encore", "talk")} for e in b.get("entries") or []]
    if not any(not e.get("talk") for e in entries):
        abort(400, "歌单是空的 · the setlist has no songs")
    show = {"id": sid, "city": city, "cityEn": city_en, "venue": (b.get("venue") or "").strip(),
            "date": dates[0], "dates": dates, "label": stop_label(dates), "labelEn": f"{len(dates)} show" + ("s" if len(dates) > 1 else "")}
    if b.get("sourceUrl"):
        show["setlistSource"] = {"label": "歌单来源", "url": b["sourceUrl"].strip()}
    show["songs"] = [{**{k: v for k, v in e.items()}, "n": "?", "clips": []} for e in entries]
    renumber(show)
    show["songs"] = [{"n": s["n"], **{k: v for k, v in s.items() if k not in ("n", "clips")}, "clips": []} for s in show["songs"]]
    path = DATA / "shows" / f"{sid}.json"
    if path.exists():
        abort(400, f"shows/{sid}.json 已存在 · file exists")
    path.write_text(dump_show(show), encoding="utf-8")
    t["shows"].append({"id": sid, "city": city, "cityEn": city_en, "date": dates[0], "dates": dates,
                       "label": show["label"], "labelEn": show["labelEn"], "aliases": [], "file": f"shows/{sid}.json"})
    t["shows"].sort(key=lambda s: s["date"])
    save_tour(t)
    return jsonify(ok=True, id=sid, show=show)


SONG_FIELDS = ("title", "cover", "note", "only", "encore", "num")   # num: own number (★, E1+E2…), empty = automatic


@app.post("/api/setlist")
def api_setlist():
    """Edit a stop's setlist. op: update (n, fields) · insert (n, where before|after, title) · delete (n; a song
    without videos that no combined entry uses) · move (n, dir -1|1; the song with its talks) · info (city, cityEn,
    dates, venue, sourceUrl). Every change renumbers and saves (with the usual backup)."""
    b = request.get_json(force=True)
    show_id, op = b.get("show"), b.get("op")
    show = load_show(show_id)
    songs = show["songs"]
    s = next((x for x in songs if x["n"] == b.get("n")), None)
    if op in ("update", "insert", "delete", "move") and (not s or not is_plain(s)):
        abort(400, "no such song")
    if op == "update":
        for k in SONG_FIELDS:
            if k in (b.get("fields") or {}):
                v = b["fields"][k]
                v = v.strip() if isinstance(v, str) else v
                if k == "num" and v and re.fullmatch(r"E?\d+", v):
                    abort(400, "编号不能是 1、2 或 E1 这种（那是自动编号），可以用 ★、E1+E2 等 · use ★, E1+E2…; plain numbers are automatic")
                if k == "title" and not v:
                    abort(400, "title is required")
                if v in ("", None, False):
                    s.pop(k, None)
                else:
                    s[k] = v
        head = {k: s[k] for k in ("n", "title") if k in s}
        rest = {k: v for k, v in s.items() if k not in ("n", "title", "clips")}
        clips = s["clips"]; s.clear(); s.update(head); s.update(rest); s["clips"] = clips
    elif op == "insert":
        title = (b.get("title") or "").strip()
        if not title:
            abort(400, "title is required")
        new = {"n": "?", "title": title, **({"encore": True} if s.get("encore") else {}), "clips": []}
        bl = blocks(songs)
        i = next(k for k, blk in enumerate(bl) if blk[0] is s)
        bl.insert(i if b.get("where") == "before" else i + 1, [new])
        songs[:] = [x for blk in bl for x in blk]
    elif op == "delete":
        if s["clips"]:
            abort(400, "这首歌还有视频，先删除或移走 · move or delete its videos first")
        if any(s["n"] in (x.get("combo") or []) for x in songs):
            abort(400, "合并条目还用着这首歌，先删除合并条目 · a combined entry uses this song")
        bl = blocks(songs)
        i = next(k for k, blk in enumerate(bl) if blk[0] is s)
        talks = bl.pop(i)[1:]                            # its talks stay, attached to the song before (or after)
        if talks:
            if i > 0:
                bl[i - 1].extend(talks)
            else:
                bl.insert(0, talks)
        songs[:] = [x for blk in bl for x in blk]
    elif op == "move":
        bl = blocks(songs)
        i = next(k for k, blk in enumerate(bl) if blk[0] is s)
        j = i + (1 if b.get("dir") == 1 else -1)
        if 0 <= j < len(bl) and is_plain(bl[j][0]):
            bl[i], bl[j] = bl[j], bl[i]
        songs[:] = [x for blk in bl for x in blk]
    elif op == "info":
        dates = parse_dates(b.get("dates")) if b.get("dates") is not None else show.get("dates") or [show["date"]]
        if not dates:
            abort(400, "dates?")
        for k in ("city", "cityEn", "venue"):
            if k in b:
                show[k] = (b[k] or "").strip()
        show.update(date=dates[0], dates=dates, label=stop_label(dates))
        if "sourceUrl" in b:
            if (b["sourceUrl"] or "").strip():
                show["setlistSource"] = {"label": (show.get("setlistSource") or {}).get("label", "歌单来源"), "url": b["sourceUrl"].strip()}
            else:
                show.pop("setlistSource", None)
        t = tour()
        entry = next(x for x in t["shows"] if x["id"] == show_id)
        entry.update(city=show["city"], cityEn=show.get("cityEn", ""), date=dates[0], dates=dates, label=show["label"])
        t["shows"].sort(key=lambda x: x["date"])
        save_tour(t)
    else:
        abort(400, "bad op")
    mapping = renumber(show)
    save_show(show_id, show)
    return jsonify(ok=True, show=show, renamed={k: v for k, v in mapping.items() if k != v})


# ---------------------------------------------------------------- publish (git commit + push → Cloudflare Pages)

GITHUB_CONFIG = Path(r"C:\AI Tools\xz-autosync\config.json")   # autosync's GitHub token (main PC); never stored in git
LIVE_SITE = "https://xz-tour.pages.dev"
PUBLISH_PATHS = ["data"]                         # what the admin edits; other files are code, published by hand


def git(*args, check=True, extra=()):
    exe = shutil.which("git")
    if not exe:
        raise RuntimeError("找不到 git · git not found")
    r = subprocess.run([exe, *extra, *args], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                       timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if check and r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-400:])
    return r.stdout


def pending_changes():
    """(data files waiting to be published, other changed files that the button leaves alone)"""
    rows = [l for l in git("status", "--porcelain", "--untracked-files=all").splitlines() if l.strip()]
    paths = [l[3:].strip().strip('"') for l in rows]
    data = [p for p in paths if any(p == d or p.startswith(d + "/") for d in PUBLISH_PATHS) and not p.endswith(".tmp")]
    return data, [p for p in paths if p not in data]


def auth_header():
    tok = json.loads(GITHUB_CONFIG.read_text(encoding="utf-8")).get("github_token", "")
    if not tok:
        raise RuntimeError("autosync 的 config.json 里没有 github_token")
    import base64
    return "Authorization: Basic " + base64.b64encode(f"x-access-token:{tok}".encode()).decode()


@app.get("/api/publish/status")
def api_publish_status():
    try:
        data, other = pending_changes()
        return jsonify(ok=True, data=data, other=other)
    except (RuntimeError, subprocess.SubprocessError) as e:
        return jsonify(ok=False, error=str(e))


@app.post("/api/publish")
def api_publish():
    """Commit the changed data files and push; Cloudflare Pages deploys the push in ~30 s."""
    try:
        data, other = pending_changes()
        if not data:
            return jsonify(ok=True, published=[], other=other, message="没有要发布的改动 · nothing to publish")
        header = auth_header()
        git("fetch", "-q", "origin", extra=("-c", f"http.extraheader={header}"))
        behind = int(git("rev-list", "--count", "HEAD..origin/main").strip() or 0)
        if behind:
            return jsonify(ok=False, error=f"GitHub 上有 {behind} 个本地没有的提交，先同步再发布 · origin/main is ahead, pull first")
        git("add", "--", *data)
        names = sorted({Path(p).stem for p in data})
        git("commit", "-q", "-m", f"Publish from admin: {', '.join(names)}\n\nUpdated: {', '.join(data)}")
        git("push", "-q", "origin", "main", extra=("-c", f"http.extraheader={header}"))
        sha = git("rev-parse", "--short", "HEAD").strip()
        return jsonify(ok=True, published=data, other=other, commit=sha)
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as e:
        return jsonify(ok=False, error=str(e).replace(auth_header_mask(), "***"))


def auth_header_mask():
    try:
        return auth_header().split(" ", 2)[2]
    except Exception:
        return "\0"


@app.post("/api/publish/live")
def api_publish_live():
    """Is the live site serving our published data files yet? (compares the parsed JSON, so line endings don't matter)"""
    files = request.get_json(force=True).get("files") or []
    waiting = []
    for f in files:
        if not f.endswith(".json"):
            continue
        try:
            live = requests.get(f"{LIVE_SITE}/{f}", params={"t": int(time.time())}, headers={"User-Agent": UA,
                                "Cache-Control": "no-cache"}, timeout=15).json()
            if live != json.loads((ROOT / f).read_text(encoding="utf-8")):
                waiting.append(f)
        except (requests.RequestException, ValueError):
            waiting.append(f)
    return jsonify(ok=True, live=not waiting, waiting=waiting)


@app.post("/api/restart")
def api_restart():
    """Start a fresh copy of this server (so code changes take effect) and exit this one.
    The new process waits for the port to be free before it starts listening."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)

    def swap():
        time.sleep(0.5)                                # let this response reach the browser first
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--wait-port"],
                         cwd=str(ROOT), creationflags=flags, close_fds=True)
        os._exit(0)

    threading.Thread(target=swap, daemon=True).start()
    return jsonify(ok=True, pid=os.getpid())


@app.get("/api/ping")
def api_ping():
    return jsonify(ok=True, pid=os.getpid())


def wait_for_port(seconds=15):
    for _ in range(seconds * 5):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", PORT))
                return
            except OSError:
                time.sleep(0.2)


if __name__ == "__main__":
    if "--wait-port" in sys.argv:
        wait_for_port()
    app.run(host="127.0.0.1", port=PORT, debug=False)
