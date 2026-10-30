import os, re, sys, time, json, glob, asyncio, subprocess
import requests
from pyrogram import Client

TOKEN = os.environ["TG_BOT_TOKEN"]
API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
CHAT = int(os.environ["P_CHAT_ID"])
MSG = os.environ.get("P_MSG_ID", "").strip()
MSG_ID = int(MSG) if MSG.isdigit() else None
URL = os.environ["P_URL"]
TITLE = (os.environ.get("P_TITLE") or "video")[:200]
IS_CUT = bool(os.environ.get("P_YT", "").strip())
BOT = "https://api.telegram.org/bot" + TOKEN
DONE = ("completed", "complete", "done", "success", "succeeded", "finished", "ready")

try:
    X = json.loads(os.environ.get("P_X", "") or "{}")
except Exception:
    X = {}


def tg(method, **data):
    try:
        return requests.post(BOT + "/" + method, json=data, timeout=20).json()
    except Exception:
        return None


def status(text):
    if MSG_ID:
        tg("editMessageText", chat_id=CHAT, message_id=MSG_ID, text=text[:4000])


def fail(reason):
    print("FAILED:", reason)
    status("❌ Could not upload the file, the link was sent below.")
    tg("sendMessage", chat_id=CHAT, text="🎬 " + TITLE + "\n" + URL + "\n\n(Upload failed: " + str(reason)[:300] + ")")
    sys.exit(1)


def fetch_to(url, path, read_timeout):
    done, last = 0, 0.0
    with requests.get(url, stream=True, timeout=(30, read_timeout), headers={"User-Agent": "Mozilla/5.0"}) as r:
        print("download HTTP", r.status_code, r.headers.get("Content-Type"), r.headers.get("Content-Length"))
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        with open(path, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                f.write(chunk)
                done += len(chunk)
                if time.time() - last > 8:
                    last = time.time()
                    extra = (" " + str(done * 100 // total) + "%") if total else (" " + str(done // 1048576) + " MB")
                    status("⬇️ Preparing the file.." + extra)
    return done


def download(path):
    attempts = 1 if IS_CUT else 2
    read_timeout = 240 if IS_CUT else 420
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            status("⬇️ Preparing the file.." + (" (try " + str(attempt) + " of " + str(attempts) + ")" if attempt > 1 else ""))
            return fetch_to(URL, path, read_timeout)
        except Exception as e:
            last_err = e
            print("download attempt", attempt, "failed:", e)
            if attempt < attempts:
                time.sleep(15)
    raise last_err


# ---------- backup clipper (only for /cut jobs) ----------
def jfind(o, keys, depth=0):
    if depth > 6:
        return None
    if isinstance(o, dict):
        for k in keys:
            if k in o and isinstance(o[k], (str, int)) and str(o[k]).strip():
                return o[k]
        for v in o.values():
            r = jfind(v, keys, depth + 1)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = jfind(v, keys, depth + 1)
            if r is not None:
                return r
    return None


def jurls(o, depth=0, path=""):
    out = []
    if depth > 6:
        return out
    if isinstance(o, str):
        if o.startswith("http"):
            out.append((path.lower(), o))
    elif isinstance(o, dict):
        for k, v in o.items():
            out += jurls(v, depth + 1, path + "." + str(k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out += jurls(v, depth + 1, path + "." + str(i))
    return out


def clipper_fallback(path):
    yt = os.environ.get("P_YT", "").strip()
    s = os.environ.get("P_START", "").strip()
    e = os.environ.get("P_END", "").strip()
    q = os.environ.get("P_Q", "360").strip() or "360"
    key = os.environ.get("RAPIDAPI_KEY", "").strip()
    host = os.environ.get("CLIP_HOST", "").strip() or "youtube-video-clipper-api-no-downloads.p.rapidapi.com"
    if not (yt and s.isdigit() and e.isdigit() and key):
        print("clipper fallback skipped (missing data or RAPIDAPI_KEY)")
        return False
    status("🔁 The first source failed, trying the backup clipper..")
    h = {"x-rapidapi-key": key, "x-rapidapi-host": host, "Content-Type": "application/json"}
    try:
        r = requests.post("https://" + host + "/process-video", headers=h, timeout=90,
                          json={"video_url": yt, "start": int(s), "end": int(e), "video_quality": q + "p"})
        print("clipper start:", r.status_code, r.text[:500])
        job = jfind(r.json(), ("job_id", "jobId", "id"))
        if not job:
            return False
        deadline = time.time() + 600
        while time.time() < deadline:
            time.sleep(6)
            pr = requests.get("https://" + host + "/status/" + str(job), headers=h, timeout=60)
            pj = pr.json()
            print("clipper poll:", pr.status_code, str(pj)[:300])
            st = str(jfind(pj, ("status", "state")) or "").lower()
            if st in ("failed", "error", "cancelled"):
                return False
            for p, u in jurls(pj):
                if re.search(r"thumb|image|cover|poster|preview", p) or re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", u, re.I):
                    continue
                if st in DONE or ".mp4" in u.lower():
                    n = fetch_to(u, path, 300)
                    if n > 100 * 1024:
                        print("clipper download ok:", n)
                        return True
        return False
    except Exception as ex:
        print("clipper fallback failed:", ex)
        return False


# ---------- video helpers ----------
def probe(path):
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration", "-of", "json", path], timeout=60)
        j = json.loads(out)
        s = j["streams"][0]
        return int(s["width"]), int(s["height"]), int(float(j["format"]["duration"]))
    except Exception:
        return 0, 0, 0


def thumb(path):
    try:
        subprocess.run(["ffmpeg", "-y", "-ss", "2", "-i", path, "-vframes", "1", "-vf", "scale=320:-2", "thumb.jpg"],
                       check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "thumb.jpg" if os.path.exists("thumb.jpg") else None
    except Exception:
        return None


def remux(src):
    out = "fixed.mp4"
    attempts = [
        ["ffmpeg", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
         "-movflags", "+faststart", out],
        ["ffmpeg", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-movflags", "+faststart", out],
    ]
    for cmd in attempts:
        try:
            subprocess.run(cmd, check=True, timeout=1200, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(out) and os.path.getsize(out) > 100 * 1024:
                print("remux ok")
                return out
        except Exception as e:
            print("remux attempt failed:", e)
    return src


_last = [0.0]
def progress(current, total):
    if time.time() - _last[0] > 8 and total:
        _last[0] = time.time()
        status("📤 Uploading to Telegram.. " + str(current * 100 // total) + "%")


# ---------- Spotify songs: tag the MP3 with title, artist, album and embedded cover art ----------
def run_ok(cmd, out):
    try:
        if os.path.exists(out):
            os.remove(out)
        subprocess.run(cmd, check=True, timeout=300, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return os.path.exists(out) and os.path.getsize(out) > 20 * 1024
    except Exception as e:
        print("ffmpeg failed:", e)
        return False


async def audio_main():
    status("⬇️ Preparing the song..")
    try:
        size = fetch_to(URL, "song_in.mp3", 300)
    except Exception as e:
        fail("download: " + str(e))
    if size < 20 * 1024:
        fail("the MP3 link returned a tiny file, it may have expired")

    title = (X.get("track") or TITLE)[:120]
    artist = (X.get("artist") or "")[:120]
    album = (X.get("album") or "")[:120]
    try:
        dur = int(X.get("dur") or 0)
    except Exception:
        dur = 0
    cover = X.get("cover") or ""

    have_cover = False
    if cover:
        status("🖼 Adding the album art..")
        try:
            cr = requests.get(cover, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            cr.raise_for_status()
            with open("cover.jpg", "wb") as f:
                f.write(cr.content)
            have_cover = os.path.getsize("cover.jpg") > 1000
        except Exception as e:
            print("cover download failed:", e)

    meta = ["-metadata", "title=" + title, "-metadata", "artist=" + artist, "-metadata", "album=" + album]
    out = "song_out.mp3"
    ok = False
    thumb_path = None
    if have_cover:
        ok = run_ok(["ffmpeg", "-y", "-i", "song_in.mp3", "-i", "cover.jpg", "-map", "0:a", "-map", "1:v",
                     "-c", "copy", "-id3v2_version", "3"] + meta +
                    ["-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)", out], out)
        if run_ok(["ffmpeg", "-y", "-i", "cover.jpg", "-vf", "scale=320:-2", "-q:v", "4", "thumb_song.jpg"], "thumb_song.jpg"):
            thumb_path = "thumb_song.jpg"
    if not ok:
        ok = run_ok(["ffmpeg", "-y", "-i", "song_in.mp3", "-map", "0:a", "-c", "copy", "-id3v2_version", "3"] + meta + [out], out)
    final = out if ok else "song_in.mp3"

    safe = "".join(c for c in (artist + " - " + title) if c not in '\\/:*?"<>|')[:80] or "song"
    status("📤 Uploading to Telegram..")
    async with Client("uploader", api_id=API_ID, api_hash=API_HASH, bot_token=TOKEN, in_memory=True) as app:
        await app.send_audio(CHAT, final, title=title, performer=artist or None, duration=dur,
                             thumb=thumb_path, file_name=safe + ".mp3", progress=progress)
    status("done uploading! 🙊❤️")
    print("OK")

def ytdlp_download(page, height, out):
    h = str(height) if str(height).isdigit() else "720"
    for f in glob.glob("dl_video.*"):
        os.remove(f)
    fmt = "b[height<=" + h + "]/bv*[height<=" + h + "]+ba/b"
    cmd = ["yt-dlp", "--no-playlist", "--newline", "--concurrent-fragments", "4",
           "-f", fmt, "--merge-output-format", "mp4", "-o", "dl_video.%(ext)s", page]
    status("⬇️ Downloading from the source..")
    last_line, last_t, started = "", 0.0, time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in p.stdout:
        line = line.strip()
        if line:
            last_line = line
            print(line[:200])
        m = re.search(r"\[download\]\s+([\d.]+)%", line)
        if m and time.time() - last_t > 8:
            last_t = time.time()
            status("⬇️ Downloading from the source.. " + m.group(1) + "%")
        if time.time() - started > 1500:
            p.kill()
            raise RuntimeError("yt-dlp took too long")
    p.wait()
    files = [f for f in glob.glob("dl_video.*") if not f.endswith(".part") and not f.endswith(".ytdl")]
    if p.returncode != 0 or not files:
        raise RuntimeError("yt-dlp failed: " + last_line[:200])
    os.replace(files[0], out)
    return os.path.getsize(out)

async def main():
    if X.get("mode") == "audio":
        await audio_main()
        return

    path = "video.mp4"
    if X.get("mode") == "ytdlp":
        try:
            size = ytdlp_download(URL, X.get("h"), path)
        except Exception as e:
            print("yt-dlp failed:", e)
            dl = X.get("dl") or ""
            if not dl:
                fail("yt-dlp: " + str(e))
            status("🔁 Trying the backup source..")
            try:
                size = fetch_to(dl, path, 300)
            except Exception as e2:
                fail("yt-dlp: " + str(e) + " | backup: " + str(e2))
    else:
        try:
            size = download(path)
        except Exception as e:
            print("converter download failed:", e)
            if clipper_fallback(path):
                size = os.path.getsize(path)
            else:
                fail("download: " + str(e))
    if size < 100 * 1024:
        fail("the server returned a tiny file (" + str(size) + " bytes), the link may have expired")

    status("🛠 Making the video seekable..")
    path = remux(path)
    size = os.path.getsize(path)
    if size > 1950 * 1024 * 1024:
        fail("file is larger than Telegram's 2 GB limit")

    w, h, d = probe(path)
    th = thumb(path)
    safe = "".join(c for c in TITLE if c not in '\\/:*?"<>|')[:60] or "video"
    spoiler = bool(X.get("spoiler"))
    status("📤 Uploading to Telegram..")

    async with Client("uploader", api_id=API_ID, api_hash=API_HASH, bot_token=TOKEN, in_memory=True) as app:
        try:
            await app.send_video(CHAT, path, caption=TITLE, duration=d, width=w, height=h,
                                 thumb=th, supports_streaming=True, file_name=safe + ".mp4",
                                 progress=progress, has_spoiler=spoiler)
        except TypeError:
            # this Pyrogram version has no spoiler option: send normally
            await app.send_video(CHAT, path, caption=TITLE, duration=d, width=w, height=h,
                                 thumb=th, supports_streaming=True, file_name=safe + ".mp4",
                                 progress=progress)
    status("done uploading! 🙊❤️")
    print("OK")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise
    except Exception as e:
        fail(type(e).__name__ + ": " + str(e))
