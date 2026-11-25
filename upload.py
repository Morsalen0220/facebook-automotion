import os
import io
import json
import random
import re
import subprocess
import tempfile
import time
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"].strip()
CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"].strip()
REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"].strip()
FB_PAGE_ID = os.environ["FB_PAGE_ID"].strip()
FB_PAGE_ACCESS_TOKEN = os.environ["FB_PAGE_ACCESS_TOKEN"].strip()
FB_API_VERSION = os.environ.get("FB_API_VERSION", "v21.0").strip()
FALLBACK_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

# ==============================================================================
# FACEBOOK UPLOAD MODE
# - "AUTO"  : প্রথমে Reels হিসেবে চেষ্টা করবে; ভিডিও বড়/আনুভূমিক হলে সাধারণ Page Video হিসেবে আপলোড করবে
# - "REELS" : শুধুমাত্র Facebook Reels (9:16, ৩-৯০ সেকেন্ড) হিসেবে পাবলিশ করবে
# - "VIDEO" : সরাসরি Facebook Page Video হিসেবে পাবলিশ করবে (যেকোনো সাইজ বা দৈর্ঘ্যের ভিডিও)
# ==============================================================================
FB_UPLOAD_MODE = "AUTO"

# ==============================================================================
# GOOGLE DRIVE FOLDERS & ATTRIBUTION SETTINGS
# নিচে কমা (,) দিয়ে ২, ৩, ৪... ১০ বা যত খুশি ফোল্ডার যোগ করতে পারবেন।
# - "folder_id": আপনার Google Drive ফোল্ডারের ID (বা ফোল্ডারের লিংক) বসান
# - "attribution": Facebook ক্যাপশনের একদম শেষে যে লেখাটি যোগ হবে (না চাইলে "" রাখুন)
# ==============================================================================
DRIVE_FOLDERS = [
    {
        "folder_id": "1GmFpEtNuRaJK3yQiJK3XdG1fpW_3JNO5",
        "attribution": """Original Video: Ad Astra Muse - Nozima Qadamova (4K) | Early Bloomer (E65)
Creator: Ad Astra
Source: https://www.youtube.com/watch?v=IN6RBu-j6JQ
License: Creative Commons Attribution (CC BY).""",
    },
    {
        "folder_id": "1DskbS2pUpDFQeWsPjSl0ATvLpDrWWM0s",
        "attribution": """Original Video: IELTS Speaking Test- Perfect Band 9
Creator: IELTS Advantage
Source: https://www.youtube.com/watch?v=IevmdO16GuE
License: Creative Commons Attribution (CC BY).""",
    },
    {
        "folder_id": "1omq1SmE2TCpeD36wNVwbcSbNnkXhd1rn",
        "attribution": """Original Video: IELTS Speaking- Perfect Pronunciation and Fluency
Creator: IELTS Advantage
Source: https://www.youtube.com/watch?v=8pnGARTYMpg
License: Creative Commons Attribution (CC BY).""",
    },
    # নতুন ফোল্ডার যোগ করতে উপরের ব্লকের শেষে কমা (,) রেখে নিচে একইভাবে লিখুন:
    # {
    #     "folder_id": "drive_folder_id_4",
    #     "attribution": """folder 4 attribution""",
    # },
    # {
    #     "folder_id": "drive_folder_id_5",
    #     "attribution": """folder 5 attribution""",
    # },
]

STATE_FILE = "uploaded_videos.json"
REEL_CAPTION_MAX_LEN = 2200
PAGE_DESCRIPTION_MAX_LEN = 10000


def get_drive_service():
    creds = Credentials(
        token=None,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )
    return build("drive", "v3", credentials=creds)


def load_uploaded_ids():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return []
            data = json.loads(content)
            return data if isinstance(data, list) else []
    return []


def save_uploaded_ids(ids):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f, indent=2)


def smart_trim_text(text, max_len):
    """Trims text cleanly on paragraph, line, sentence, or word boundaries—never mid-word."""
    cleaned = str(text or "").strip()
    if max_len <= 0:
        return ""
    if len(cleaned) <= max_len:
        return cleaned

    cut = cleaned[:max_len]
    min_keep = max(1, int(max_len * 0.55))

    for sep in ("\n\n", "\n"):
        idx = cut.rfind(sep)
        if idx >= min_keep:
            return cut[:idx].rstrip()

    sentence_end = max(cut.rfind("।"), cut.rfind("!"), cut.rfind("?"), cut.rfind("."))
    if sentence_end >= min_keep:
        return cut[: sentence_end + 1].rstrip()

    space_idx = cut.rfind(" ")
    if space_idx >= min_keep:
        return cut[:space_idx].rstrip()

    return cut.rstrip()


def clean_title(title, fallback_title, max_len=250):
    raw = str(title or fallback_title).strip()
    cleaned = re.sub(r"\s+", " ", raw).strip(' "\'“”')
    if not cleaned:
        cleaned = re.sub(r"\s+", " ", str(fallback_title)).strip() or "শিক্ষামূলক ভিডিও"
    return smart_trim_text(cleaned, max_len)


def clean_description(description):
    text = str(description or "")
    text = text.replace("\\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def split_trailing_hashtags(text):
    """Separates a trailing hashtag block so hashtags stay intact if body trimming is ever needed."""
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    if len(blocks) < 2:
        return text, ""

    last_block = blocks[-1]
    tokens = last_block.split()
    if tokens and all(tok.startswith("#") for tok in tokens):
        return "\n\n".join(blocks[:-1]).strip(), last_block
    return text, ""


def format_facebook_caption(body, attribution="", max_len=PAGE_DESCRIPTION_MAX_LEN):
    """
    Combines caption body and attribution without ever cutting off attribution,
    hashtags, or mid-sentence text.
    """
    base_body = clean_description(body)
    attr_text = clean_description(attribution)

    if not attr_text:
        if len(base_body) <= max_len:
            return base_body
        main_part, hashtags_part = split_trailing_hashtags(base_body)
        if hashtags_part and len(hashtags_part) + 2 < max_len:
            budget = max_len - len(hashtags_part) - 2
            trimmed_main = smart_trim_text(main_part, budget)
            return f"{trimmed_main}\n\n{hashtags_part}" if trimmed_main else hashtags_part[:max_len]
        return smart_trim_text(base_body, max_len)

    combined = f"{base_body}\n\n{attr_text}" if base_body else attr_text
    if len(combined) <= max_len:
        return combined

    if len(attr_text) >= max_len:
        return smart_trim_text(attr_text, max_len)

    available_for_body = max_len - len(attr_text) - 2
    if available_for_body <= 0:
        return attr_text

    main_part, hashtags_part = split_trailing_hashtags(base_body)
    if hashtags_part and len(hashtags_part) + 2 < available_for_body:
        main_budget = available_for_body - len(hashtags_part) - 2
        trimmed_main = smart_trim_text(main_part, main_budget)
        if trimmed_main:
            return f"{trimmed_main}\n\n{hashtags_part}\n\n{attr_text}"

    trimmed_body = smart_trim_text(base_body, available_for_body)
    return f"{trimmed_body}\n\n{attr_text}" if trimmed_body else attr_text


def build_reel_caption(title, description, attribution=""):
    clean_t = str(title or "").strip()
    clean_d = clean_description(description)
    if clean_t and not clean_d.lower().startswith(clean_t.lower()):
        reel_body = f"{clean_t}\n\n{clean_d}".strip()
    else:
        reel_body = clean_d or clean_t
    return format_facebook_caption(reel_body, attribution, max_len=REEL_CAPTION_MAX_LEN)


def parse_metadata_json(raw_text, fallback_title):
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL | re.IGNORECASE).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE).strip()

    try:
        data = json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0), strict=False)

    if not isinstance(data, dict):
        raise ValueError("Metadata response JSON is not an object.")

    raw_title = str(data.get("title") or "").strip()
    raw_desc = clean_description(data.get("description") or "")

    if not raw_desc or len(raw_desc) < 50:
        raise ValueError("Generated description is missing or incomplete.")

    title = clean_title(raw_title, fallback_title)
    return title, raw_desc


def get_available_gemini_models(headers):
    fallback_models = [
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-flash-latest",
    ]
    try:
        res = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers=headers,
            timeout=20,
        )
        if res.status_code != 200:
            print(f"[Gemini Models List] Error {res.status_code}: {res.text[:200]}")
            return fallback_models

        models_data = res.json().get("models", [])
        blocked_keywords = (
            "tts", "image", "audio", "live", "embed", "vision",
            "computer-use", "robotics", "1.5", "2.0", "8b", "exp"
        )

        candidates = []
        for m in models_data:
            methods = m.get("supportedGenerationMethods", [])
            if "generateContent" not in methods:
                continue
            mid = m.get("name", "").replace("models/", "").strip()
            low = mid.lower()
            if not low.startswith("gemini-") or "flash" not in low:
                continue
            if any(b in low for b in blocked_keywords):
                continue
            candidates.append(mid)

        if not candidates:
            return fallback_models

        def gemini_priority(mid):
            low = mid.lower()
            ver_match = re.match(r"^gemini-(\d+(?:\.\d+)?)-", low)
            version = float(ver_match.group(1)) if ver_match else (2.9 if "latest" in low else 0.0)
            is_preview = 1 if "preview" in low else 0
            is_lite = 1 if "lite" in low else 0
            is_dated = 1 if re.search(r"-(\d{3}|\d{2}-\d{4}|\d{4}-\d{2})$", low) else 0
            return (-version, is_preview, is_lite, is_dated, low)

        modern = [c for c in candidates if not c.startswith("gemini-2.5")]
        pool = modern if modern else candidates
        pool.sort(key=gemini_priority)

        base_set = set(pool)
        deduped = [
            m for m in pool
            if not (
                re.search(r"-(\d{3}|\d{2}-\d{4}|\d{4}-\d{2})$", m)
                and re.sub(r"-(\d{3}|\d{2}-\d{4}|\d{4}-\d{2})$", "", m) in base_set
            )
        ]

        selected = (deduped or pool)[:4]
        print(f"Discovered active Gemini models: {selected}")
        return selected
    except Exception as e:
        print(f"[Gemini Models List] Exception: {e}")
        return fallback_models


def generate_with_gemini(prompt, fallback_title):
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY is not set in GitHub Secrets. Skipping Gemini.")
        return None

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.7,
            "maxOutputTokens": 4096,
        },
    }

    models = get_available_gemini_models(headers)
    max_rounds = 3

    for attempt in range(1, max_rounds + 1):
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                res = requests.post(url, headers=headers, json=payload, timeout=45)
                if res.status_code != 200:
                    print(f"[Gemini {attempt}/{max_rounds} | {model}] API Error {res.status_code}: {res.text[:200]}")
                    continue

                candidates = res.json().get("candidates", [])
                if not candidates:
                    print(f"[Gemini {attempt}/{max_rounds} | {model}] Empty candidates returned.")
                    continue

                finish_reason = candidates[0].get("finishReason", "")
                if finish_reason == "MAX_TOKENS":
                    print(f"[Gemini {attempt}/{max_rounds} | {model}] Truncated by MAX_TOKENS. Retrying next model...")
                    continue

                parts = candidates[0].get("content", {}).get("parts", [])
                text_parts = [p["text"] for p in parts if "text" in p and not p.get("thought")]
                if not text_parts:
                    text_parts = [p["text"] for p in parts if "text" in p]
                raw_text = "\n".join(text_parts).strip()

                result = parse_metadata_json(raw_text, fallback_title)
                print(f"Metadata generated successfully using Gemini ({model}, attempt {attempt}).")
                return result
            except Exception as e:
                print(f"[Gemini {attempt}/{max_rounds} | {model}] Exception: {e}")

        if attempt < max_rounds:
            wait_seconds = attempt * 10
            print(f"Gemini busy. Waiting {wait_seconds} seconds before retry...")
            time.sleep(wait_seconds)

    return None


def get_available_groq_models(headers):
    fallback_models = [
        "llama-3.3-70b-versatile",
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "qwen/qwen3-32b",
    ]
    try:
        res = requests.get("https://api.groq.com/openai/v1/models", headers=headers, timeout=20)
        if res.status_code != 200:
            print(f"[Groq Models List] Error {res.status_code}: {res.text[:200]}")
            return fallback_models

        models_data = res.json().get("data", [])
        blocked_keywords = (
            "whisper", "tts", "guard", "safeguard", "embed",
            "audio", "speech", "playai", "orpheus", "moderation", "allam"
        )

        candidates = []
        for m in models_data:
            mid = m.get("id", "")
            if not mid or m.get("active") is False:
                continue
            low = mid.lower()
            if any(b in low for b in blocked_keywords):
                continue
            candidates.append(mid)

        if not candidates:
            return fallback_models

        def model_priority(mid):
            low = mid.lower()
            if "llama-4" in low or "maverick" in low or "scout" in low:
                return (0, mid)
            if "qwen" in low:
                return (1, mid)
            if "gpt-oss" in low:
                return (2, mid)
            if "llama" in low and ("70b" in low or "versatile" in low):
                return (3, mid)
            if "kimi" in low or "deepseek" in low:
                return (4, mid)
            if "gemma" in low or "mistral" in low:
                return (5, mid)
            if "llama" in low:
                return (6, mid)
            if "compound" in low:
                return (8, mid)
            return (7, mid)

        candidates.sort(key=model_priority)
        print(f"Discovered active Groq models: {candidates[:8]}")
        return candidates[:6]
    except Exception as e:
        print(f"[Groq Models List] Exception: {e}")
        return fallback_models


def generate_with_groq(prompt, fallback_title):
    if not GROQ_API_KEY:
        print("GROQ_API_KEY is not set in GitHub Secrets. Skipping Groq fallback.")
        return None

    print("Switching to Groq API fallback...")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GROQ_API_KEY}",
    }

    groq_models = get_available_groq_models(headers)
    if not groq_models:
        return None

    for model in groq_models:
        base_payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert Bengali Facebook Page & Reels growth strategist. "
                        "Always respond strictly with valid JSON containing keys: title, description. "
                        "Write natural, complete, well-formatted Bengali captions without cutting off any section or hashtag."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "temperature": 0.7,
            "max_tokens": 2048,
        }

        for use_json_mode in (True, False):
            payload = dict(base_payload)
            if use_json_mode:
                payload["response_format"] = {"type": "json_object"}

            try:
                res = requests.post(url, headers=headers, json=payload, timeout=40)
                if res.status_code == 400 and use_json_mode:
                    continue
                if res.status_code != 200:
                    print(f"[Groq | {model}] API Error {res.status_code}: {res.text[:200]}")
                    break

                choice = res.json()["choices"][0]
                if choice.get("finish_reason") == "length":
                    print(f"[Groq | {model}] Output truncated (finish_reason=length). Trying next model...")
                    break

                raw_text = choice["message"]["content"]
                result = parse_metadata_json(raw_text, fallback_title)
                print(f"Metadata generated successfully using Groq ({model}).")
                return result
            except Exception as e:
                print(f"[Groq | {model}] Exception: {e}")
                break

    return None


def generate_facebook_metadata(filename):
    fallback_title = os.path.splitext(filename)[0].strip()
    prompt = (
        "You are a top Bengali Facebook Page & Reels viral content strategist for an educational page. "
        "Based on the video filename below, generate highly engaging, complete Bengali metadata:\n\n"
        "1. title:\n"
        "   - Write an attention-grabbing, curiosity-driven Bengali video title (50 to 85 characters) with the main keyword near the start.\n"
        "   - Must be a complete, natural Bengali title.\n\n"
        "2. description:\n"
        "   - Write a visually formatted, high-engagement Bengali Facebook caption/description (120 to 180 words, strictly under 1350 characters total) using blank lines (\\n\\n) between sections:\n"
        "     * Scroll-Stopping Hook (first 1-2 lines): Strong, intriguing Bengali hook so viewers stop scrolling (do NOT repeat the exact title).\n"
        "     * সংক্ষিপ্ত আলোচনা (2-3 lines): Explain the core topic simply and engagingly in fluent Bengali.\n"
        "     * 📌 মূল পয়েন্টসমূহ: 3 to 4 crisp bullet points starting with ✅.\n"
        "     * 💬 এনগেজমেন্ট প্রশ্ন ও CTA: Ask viewers an interesting question to boost comments, and invite them to Like, Share, and Follow the page for more educational videos.\n"
        "     * Hashtags at the bottom: 5 to 7 relevant hashtags on the last line (e.g., #reels #facebookreels #bangla #শিক্ষামূলক plus topic hashtags).\n\n"
        "Strict Rules:\n"
        "- Regardless of whether the filename is in English, Bangla, or Banglish, ALWAYS write title and description strictly in natural, fluent Bengali.\n"
        "- Keep the description concise enough (under 1350 characters) so every section, sentence, and hashtag finishes completely.\n"
        "- Return ONLY valid JSON with exact keys: title, description.\n\n"
        f"Video Filename: {filename}"
    )

    result = generate_with_gemini(prompt, fallback_title)
    if result is not None:
        return result

    result = generate_with_groq(prompt, fallback_title)
    if result is not None:
        return result

    return None


def download_video(drive, file_id, dest_path):
    request = drive.files().get_media(fileId=file_id)
    with io.FileIO(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=10 * 1024 * 1024)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"Downloading from Drive: {int(status.progress() * 100)}%")
    if not os.path.exists(dest_path) or os.path.getsize(dest_path) <= 0:
        raise RuntimeError("Downloaded video file is empty (0 bytes).")


def safe_json(res, label):
    try:
        return res.json()
    except Exception:
        raise RuntimeError(f"{label} returned non-JSON ({res.status_code}): {res.text[:250]}")


def inspect_video_specs(video_path):
    """Returns (width, height, duration_sec) using ffprobe if available, else None."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration",
            "-of", "json",
            video_path,
        ]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if out.returncode != 0:
            return None
        info = json.loads(out.stdout)
        streams = info.get("streams", [])
        if not streams:
            return None
        width = int(streams[0].get("width", 0))
        height = int(streams[0].get("height", 0))
        duration = float(info.get("format", {}).get("duration", 0.0))
        return width, height, duration
    except Exception:
        return None


def upload_facebook_reel(video_path, description):
    file_size = os.path.getsize(video_path)
    init_finish_url = f"https://graph.facebook.com/{FB_API_VERSION}/{FB_PAGE_ID}/video_reels"

    print("Initializing Facebook Reel upload session...")
    init_res = safe_json(
        requests.post(
            init_finish_url,
            data={"upload_phase": "start", "access_token": FB_PAGE_ACCESS_TOKEN},
            timeout=30,
        ),
        "Reel init",
    )

    if "video_id" not in init_res:
        raise RuntimeError(f"Reel init failed: {init_res}")

    video_id = init_res["video_id"]
    rupload_url = f"https://rupload.facebook.com/video-upload/{FB_API_VERSION}/{video_id}"
    headers = {
        "Authorization": f"OAuth {FB_PAGE_ACCESS_TOKEN}",
        "offset": "0",
        "file_size": str(file_size),
    }

    print(f"Uploading Reel binary ({file_size} bytes)...")
    with open(video_path, "rb") as vf:
        up_res = safe_json(
            requests.post(rupload_url, headers=headers, data=vf, timeout=300),
            "Reel binary transfer",
        )

    if not up_res.get("success"):
        raise RuntimeError(f"Reel binary transfer failed: {up_res}")

    print("Publishing Facebook Reel...")
    finish_res = safe_json(
        requests.post(
            init_finish_url,
            data={
                "upload_phase": "finish",
                "video_state": "PUBLISHED",
                "description": description,
                "video_id": video_id,
                "access_token": FB_PAGE_ACCESS_TOKEN,
            },
            timeout=60,
        ),
        "Reel finish",
    )

    if not finish_res.get("success"):
        raise RuntimeError(f"Reel finish failed: {finish_res}")

    # সংক্ষিপ্ত স্ট্যাটাস চেক: Facebook অ্যাসিঙ্ক্রোনাস প্রসেসিংয়ে সাথে সাথে এরর দিলে ধরতে পারবে
    status_url = f"https://graph.facebook.com/{FB_API_VERSION}/{video_id}"
    for _ in range(3):
        time.sleep(4)
        st_res = requests.get(
            status_url,
            params={"fields": "status", "access_token": FB_PAGE_ACCESS_TOKEN},
            timeout=20,
        )
        if st_res.status_code != 200:
            break
        st_data = st_res.json().get("status", {})
        video_status = st_data.get("video_status", "")
        pub_status = st_data.get("publishing_phase", {}).get("status", "")
        proc_status = st_data.get("processing_phase", {}).get("status", "")
        if video_status == "error" or pub_status == "error" or proc_status == "error":
            raise RuntimeError(f"Facebook Reel processing rejected video: {st_data}")
        if pub_status == "complete" or video_status == "ready":
            break

    print(f"Published as Facebook Reel! Video ID: {video_id}")
    return video_id


def upload_facebook_page_video(video_path, title, description):
    file_size = os.path.getsize(video_path)
    base_url = f"https://graph-video.facebook.com/{FB_API_VERSION}/{FB_PAGE_ID}/videos"

    print("Initializing Facebook Page Video resumable upload...")
    start_res = safe_json(
        requests.post(
            base_url,
            data={
                "upload_phase": "start",
                "file_size": str(file_size),
                "access_token": FB_PAGE_ACCESS_TOKEN,
            },
            timeout=30,
        ),
        "Page video start",
    )

    if "upload_session_id" not in start_res:
        raise RuntimeError(f"Page video start failed: {start_res}")

    upload_session_id = start_res["upload_session_id"]
    video_id = start_res.get("video_id", "")
    start_offset = int(start_res["start_offset"])
    end_offset = int(start_res["end_offset"])

    with open(video_path, "rb") as vf:
        while start_offset < end_offset:
            vf.seek(start_offset)
            chunk_data = vf.read(end_offset - start_offset)

            transfer_res = safe_json(
                requests.post(
                    base_url,
                    data={
                        "upload_phase": "transfer",
                        "upload_session_id": upload_session_id,
                        "start_offset": str(start_offset),
                        "access_token": FB_PAGE_ACCESS_TOKEN,
                    },
                    files={"video_file_chunk": ("chunk.mp4", chunk_data, "application/octet-stream")},
                    timeout=300,
                ),
                "Page video chunk transfer",
            )

            if "start_offset" not in transfer_res or "end_offset" not in transfer_res:
                raise RuntimeError(f"Page video chunk transfer failed: {transfer_res}")

            start_offset = int(transfer_res["start_offset"])
            end_offset = int(transfer_res["end_offset"])
            progress = int((min(start_offset, file_size) / max(file_size, 1)) * 100)
            print(f"Uploading to Facebook Page: {progress}%")

    finish_res = safe_json(
        requests.post(
            base_url,
            data={
                "upload_phase": "finish",
                "upload_session_id": upload_session_id,
                "title": title,
                "description": description,
                "published": "true",
                "access_token": FB_PAGE_ACCESS_TOKEN,
            },
            timeout=60,
        ),
        "Page video finish",
    )

    if not finish_res.get("success"):
        raise RuntimeError(f"Page video finish failed: {finish_res}")

    print(f"Published as Facebook Page Video! Video ID: {video_id}")
    return video_id


def upload_to_facebook(video_path, title, description, attribution=""):
    mode = FB_UPLOAD_MODE.upper().strip()
    reel_caption = build_reel_caption(title, description, attribution)
    page_description = format_facebook_caption(description, attribution, max_len=PAGE_DESCRIPTION_MAX_LEN)

    if mode == "REELS":
        return upload_facebook_reel(video_path, reel_caption)
    if mode == "VIDEO":
        return upload_facebook_page_video(video_path, title, page_description)

    # AUTO mode: প্রথমে ভিডিওর দৈর্ঘ্য ও অনুপাত (9:16, ৩-৯০ সেকেন্ড) চেক করবে
    specs = inspect_video_specs(video_path)
    if specs is not None:
        width, height, duration = specs
        print(f"Detected video specs: {width}x{height}, duration={duration:.1f}s")
        if width > height or duration < 3.0 or duration > 90.0:
            print("Video is horizontal or outside 3-90s Reel limit. Uploading directly as Facebook Page Video...")
            return upload_facebook_page_video(video_path, title, page_description)

    try:
        return upload_facebook_reel(video_path, reel_caption)
    except Exception as e:
        print(f"Reels upload skipped/failed ({e}). Falling back to regular Facebook Page Video...")
        return upload_facebook_page_video(video_path, title, page_description)


def list_drive_videos(drive, folder_id):
    query = f"'{folder_id}' in parents and mimeType contains 'video/' and trashed = false"
    files = []
    page_token = None

    while True:
        results = drive.files().list(
            q=query,
            pageSize=1000,
            fields="nextPageToken, files(id, name)",
            orderBy="createdTime asc",
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break

    return files


def extract_folder_id(raw_value):
    val = str(raw_value or "").strip()
    low = val.lower()
    if not val or low.startswith("drive_folder_id") or low.startswith("your_"):
        return ""
    for pattern in (r"/folders/([a-zA-Z0-9_-]+)", r"[?&]id=([a-zA-Z0-9_-]+)"):
        match = re.search(pattern, val)
        if match:
            return match.group(1)
    return val if re.fullmatch(r"[a-zA-Z0-9_-]+", val) else ""


def get_configured_folders():
    active = []
    for idx, entry in enumerate(DRIVE_FOLDERS, start=1):
        folder_id = extract_folder_id(entry.get("folder_id", ""))
        if not folder_id:
            continue
        attribution = str(entry.get("attribution", "") or "").strip()
        active.append({
            "index": idx,
            "folder_id": folder_id,
            "attribution": attribution,
        })

    if not active and FALLBACK_FOLDER_ID:
        fallback_id = extract_folder_id(FALLBACK_FOLDER_ID)
        if fallback_id:
            active.append({
                "index": 1,
                "folder_id": fallback_id,
                "attribution": "",
            })

    return active


def main():
    configured_folders = get_configured_folders()
    if not configured_folders:
        print("No valid Google Drive folder IDs configured in DRIVE_FOLDERS.")
        return

    drive = get_drive_service()
    uploaded_ids = load_uploaded_ids()
    uploaded_set = set(uploaded_ids)

    shuffled_folders = list(configured_folders)
    random.shuffle(shuffled_folders)

    selected_folder = None
    selected_video = None

    for folder in shuffled_folders:
        folder_id = folder["folder_id"]
        try:
            files = list_drive_videos(drive, folder_id)
        except Exception as e:
            print(f"[Warning] Failed to read Folder #{folder['index']} ({folder_id}): {e}")
            continue
        new_files = [f for f in files if f["id"] not in uploaded_set]
        if new_files:
            selected_folder = folder
            selected_video = new_files[0]
            break
        print(f"No new videos in Folder #{folder['index']} ({folder_id}). Checking next folder...")

    if not selected_video:
        print("No new videos found in any configured Drive folder.")
        return

    file_id = selected_video["id"]
    file_name = selected_video["name"]
    attribution = selected_folder["attribution"]
    temp_path = os.path.join(tempfile.gettempdir(), "temp_fb_video.mp4")

    print(f"\nSelected Folder #{selected_folder['index']} ({selected_folder['folder_id']})")
    print(f"Processing: {file_name}")
    metadata = generate_facebook_metadata(file_name)

    if metadata is None:
        print("Both Gemini and Groq APIs failed. Skipping upload for this run so it retries cleanly next time.")
        return

    title, description = metadata
    print(f"Generated Title: {title}")
    if attribution:
        print(f"Attribution Ready ({len(attribution)} chars)")

    try:
        download_video(drive, file_id, temp_path)
        upload_to_facebook(temp_path, title, description, attribution)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    uploaded_ids.append(file_id)
    save_uploaded_ids(uploaded_ids)


if __name__ == "__main__":
    main()
