import os, sys, time, json, asyncio, subprocess
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
BOT = "https://api.telegram.org/bot" + TOKEN


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


def download(path):
    done, last = 0, 0.0
    with requests.get(URL, stream=True, timeout=60, headers={"User-Agent": "Mozilla/5.0"}) as r:
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


_last = [0.0]
def progress(current, total):
    if time.time() - _last[0] > 8 and total:
        _last[0] = time.time()
        status("📤 Uploading to Telegram.. " + str(current * 100 // total) + "%")


def remux(src):
    out = "fixed.mp4"
    attempts = [
        # 1) lossless: copy streams into a clean, seekable MP4
        ["ffmpeg", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
         "-movflags", "+faststart", out],
        # 2) only if that fails: re-encode
        ["ffmpeg", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-movflags", "+faststart", out],
    ]
    for cmd in attempts:
        try:
            subprocess.run(cmd, check=True, timeout=1200,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(out) and os.path.getsize(out) > 100 * 1024:
                print("remux ok")
                return out
        except Exception as e:
            print("remux attempt failed:", e)
    return src


async def main():
    path = "video.mp4"
    status("⬇️ Preparing the file..")
    try:
        size = download(path)
    except Exception as e:
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
    status("📤 Uploading to Telegram..")

    async with Client("uploader", api_id=API_ID, api_hash=API_HASH, bot_token=TOKEN, in_memory=True) as app:
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
