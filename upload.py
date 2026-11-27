import base64
import html
import io
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
import requests
from PIL import Image, ImageEnhance, ImageFilter, ImageStat
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
]

STATE_FILE = "uploaded_videos.json"
REEL_CAPTION_MAX_LEN = 2200
PAGE_DESCRIPTION_MAX_LEN = 10000

VIRAL_THUMB_THEMES = [
    {
        "name": "mrbeast_yellow",
        "badge_bg": "linear-gradient(135deg, #FF0844 0%, #FF5500 100%)",
        "badge_text": "#FFFFFF",
        "badge_border": "#FFFFFF",
        "badge_glow": "rgba(255, 20, 68, 0.70)",
        "tape_bg": "linear-gradient(135deg, #FFE600 0%, #FFB800 100%)",
        "tape_text": "#080808",
        "tape_stroke": "0px transparent",
        "tape_border": "#000000",
        "accent_glow": "rgba(255, 214, 0, 0.36)",
        "frame_border": "#FFD600",
    },
    {
        "name": "crimson_shock",
        "badge_bg": "linear-gradient(135deg, #101218 0%, #1E2230 100%)",
        "badge_text": "#FFE600",
        "badge_border": "#FFE600",
        "badge_glow": "rgba(255, 214, 0, 0.55)",
        "tape_bg": "linear-gradient(135deg, #FF0F39 0%, #D6002A 100%)",
        "tape_text": "#FFFFFF",
        "tape_stroke": "6px #000000",
        "tape_border": "#FFFFFF",
        "accent_glow": "rgba(255, 15, 57, 0.40)",
        "frame_border": "#FF1744",
    },
    {
        "name": "neon_emerald",
        "badge_bg": "linear-gradient(135deg, #FF1744 0%, #FF6D00 100%)",
        "badge_text": "#FFFFFF",
        "badge_border": "#FFFFFF",
        "badge_glow": "rgba(255, 23, 68, 0.65)",
        "tape_bg": "linear-gradient(135deg, #69F0AE 0%, #00E676 100%)",
        "tape_text": "#05140B",
        "tape_stroke": "0px transparent",
        "tape_border": "#000000",
        "accent_glow": "rgba(0, 230, 118, 0.32)",
        "frame_border": "#00E676",
    },
    {
        "name": "electric_cyan",
        "badge_bg": "linear-gradient(135deg, #7C4DFF 0%, #D500F9 100%)",
        "badge_text": "#FFFFFF",
        "badge_border": "#FFFFFF",
        "badge_glow": "rgba(213, 0, 249, 0.65)",
        "tape_bg": "linear-gradient(135deg, #18FFFF 0%, #00E5FF 100%)",
        "tape_text": "#041016",
        "tape_stroke": "0px transparent",
        "tape_border": "#000000",
        "accent_glow": "rgba(0, 229, 255, 0.34)",
        "frame_border": "#00E5FF",
    },
]


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


def clean_hook_phrase(text, max_words=4, max_chars=24):
    raw = re.sub(r"#\S+", "", str(text or "")).replace("<", "").replace(">", "")
    raw = re.sub(r"\s+", " ", raw).strip(' "\'“”|-–—')
    if not raw:
        return ""
    words = raw.split(" ")
    if len(words) > max_words:
        raw = " ".join(words[:max_words])
    if len(raw) > max_chars:
        cut = raw[:max_chars].rsplit(" ", 1)[0].strip()
        raw = cut if len(cut) >= 6 else raw[:max_chars].strip()
    return raw


def build_hook_data(data_dict, clean_video_title):
    default_badges = [
        "🔥 ৯৯% মানুষ জানে না!",
        "😱 অবিশ্বাস্য সত্য!",
        "🚨 মারাত্মক ভুল!",
        "🎯 গোপন কৌশল!",
    ]
    badge = clean_hook_phrase(data_dict.get("hook_badge", ""), max_words=4, max_chars=24)
    if not badge:
        badge = random.choice(default_badges)
    elif not any(ord(ch) > 0x2600 for ch in badge):
        badge = f"🔥 {badge}"

    line1 = clean_hook_phrase(data_dict.get("hook_line1", ""), max_words=4, max_chars=22)
    line2 = clean_hook_phrase(data_dict.get("hook_line2", ""), max_words=4, max_chars=22)

    if not line1 or not line2:
        stripped_title = re.sub(r"#\S+", "", clean_video_title)
        stripped_title = re.sub(r"\s+", " ", stripped_title).strip(" |-–—!?")
        parts = [p.strip() for p in re.split(r"[|!?।:—–-]+", stripped_title) if p.strip()]
        if len(parts) >= 2:
            line1 = line1 or clean_hook_phrase(parts[0], max_words=3, max_chars=20)
            line2 = line2 or (clean_hook_phrase(parts[1], max_words=4, max_chars=22) + "!")
        else:
            words = stripped_title.split()
            mid = max(1, min(3, len(words) // 2 or 1))
            line1 = line1 or clean_hook_phrase(" ".join(words[:mid]), max_words=3, max_chars=20)
            rem = " ".join(words[mid:]) if len(words) > mid else "এখনই জেনে নিন!"
            line2 = line2 or clean_hook_phrase(rem, max_words=4, max_chars=22)

    if not line1:
        line1 = "সফল হতে চাইলে"
    if not line2:
        line2 = "এই ১টি কাজ করুন!"
    if not line2.endswith(("!", "?", "।")):
        line2 = f"{line2}!"

    return {"badge": badge, "line1": line1, "line2": line2}


def split_trailing_hashtags(text):
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    if len(blocks) < 2:
        return text, ""

    last_block = blocks[-1]
    tokens = last_block.split()
    if tokens and all(tok.startswith("#") for tok in tokens):
        return "\n\n".join(blocks[:-1]).strip(), last_block
    return text, ""


def format_facebook_caption(body, attribution="", max_len=PAGE_DESCRIPTION_MAX_LEN):
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
    hooks = build_hook_data(data, title)
    return title, raw_desc, hooks


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
                        "You are an expert Bengali Facebook Page & Reels growth strategist and viral thumbnail hook copywriter. "
                        "Always respond strictly with valid JSON containing keys: title, description, hook_badge, hook_line1, hook_line2. "
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
        "You are a top Bengali Facebook Page & Reels viral content strategist and thumbnail hook copywriter for an educational page. "
        "Based on the video filename below, generate highly engaging Bengali metadata AND scroll-stopping Reel cover hook text:\n\n"
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
        "3. VIRAL REELS THUMBNAIL HOOK TEXT (CRITICAL - Must make every viewer stop scrolling instantly!):\n"
        "   - hook_badge: 2 to 3 ultra-punchy Bengali curiosity/shock words starting with ONE emoji from [🔥, 😱, 🚨, 🎯] (e.g., '🔥 ৯৯% মানুষ জানে না!', '😱 মারাত্মক ভুল!', '🎯 গোপন কৌশল!'). Max 20 characters.\n"
        "   - hook_line1: 2 to 3 short, bold Bengali words creating tension, question, or problem (e.g., 'আত্মবিশ্বাসের ভয়?', 'ইংরেজিতে কথা বলুন', 'স্বপ্ন পূরণের পথে'). Max 18 characters, NO hashtags.\n"
        "   - hook_line2: 2 to 4 explosive Bengali payoff/climax words ending with '!' or '?' (e.g., '১ মিনিটে জয় করুন!', 'কোনো ভয় ছাড়াই!', 'এই ১টি কাজ করুন!'). Max 20 characters, NO hashtags.\n\n"
        "Strict Rules:\n"
        "- Regardless of whether the filename is in English, Bangla, or Banglish, ALWAYS write title, description, and hook fields strictly in natural, fluent Bengali.\n"
        "- Keep the description concise enough (under 1350 characters) so every section, sentence, and hashtag finishes completely.\n"
        "- Return ONLY valid JSON with exact keys: title, description, hook_badge, hook_line1, hook_line2.\n\n"
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


def estimate_skin_presence(rgb_crop):
    tiny = rgb_crop.convert("RGB").resize((64, 64))
    raw = tiny.tobytes()
    total = len(raw) // 3
    skin_count = 0
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        if r > 90 and g > 40 and b > 20 and r > g and (r - g) >= 12 and (r - b) >= 20 and r < 248:
            skin_count += 1
    return skin_count / max(total, 1)


def score_candidate_frame(img):
    small = img.convert("RGB")
    small.thumbnail((480, 480))
    w, h = small.size

    full_gray = small.convert("L")
    full_lum = ImageStat.Stat(full_gray).mean[0]

    subject_box = (int(w * 0.14), int(h * 0.15), int(w * 0.86), int(h * 0.48))
    subject = small.crop(subject_box)

    gray = subject.convert("L")
    stat = ImageStat.Stat(gray)
    mean_lum = stat.mean[0]
    contrast = stat.stddev[0]

    if mean_lum < 28 or mean_lum > 238:
        exposure_weight = 0.25
    elif 55 <= mean_lum <= 200:
        exposure_weight = 1.0
    else:
        exposure_weight = 0.65

    edges = gray.filter(ImageFilter.FIND_EDGES)
    sharpness = ImageStat.Stat(edges).mean[0]

    hsv = subject.convert("HSV")
    saturation = ImageStat.Stat(hsv).mean[1]

    skin_ratio = estimate_skin_presence(subject)
    face_bonus = 1.0 + min(skin_ratio * 3.0, 1.10)

    template_bonus = 1.0
    if h > w:
        top_lum = ImageStat.Stat(full_gray.crop((0, 0, w, int(h * 0.13)))).mean[0]
        sub_crop = full_gray.crop((int(w * 0.12), int(h * 0.51), int(w * 0.88), int(h * 0.65)))
        sub_stat = ImageStat.Stat(sub_crop)
        if top_lum > 175 and sub_stat.mean[0] > 175:
            template_bonus = 2.2
            if sub_stat.stddev[0] > 16:
                template_bonus = 2.8
        elif full_lum < 65 and skin_ratio < 0.03:
            template_bonus = 0.15

    return (sharpness * 2.2 + contrast * 0.9 + saturation * 0.35) * exposure_weight * face_bonus * template_bonus


def extract_best_video_frame(video_path, duration, work_dir):
    safe_dur = max(duration or 30.0, 1.0)
    ratios = [0.12, 0.20, 0.28, 0.36, 0.44, 0.52, 0.60, 0.68]
    best_path = None
    best_score = -1.0

    for idx, r in enumerate(ratios):
        ts = max(0.2, min(safe_dur - 0.2, safe_dur * r))
        frame_path = os.path.join(work_dir, f"cand_{idx}.jpg")
        cmd = [
            "ffmpeg", "-y", "-ss", f"{ts:.2f}",
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            frame_path,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=15)
            if os.path.exists(frame_path) and os.path.getsize(frame_path) > 1000:
                with Image.open(frame_path) as im:
                    sc = score_candidate_frame(im)
                if sc > best_score:
                    best_score = sc
                    best_path = frame_path
        except Exception:
            continue

    return best_path


def find_headless_browser():
    candidates = [
        "google-chrome",
        "google-chrome-stable",
        "chromium-browser",
        "chromium",
        "microsoft-edge",
        "microsoft-edge-stable",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if os.path.isabs(c) and os.path.exists(c):
            return c
        found = shutil.which(c)
        if found:
            return found
    return None


def render_badge_with_twemoji(badge_text):
    parts = []
    for ch in badge_text:
        cp = ord(ch)
        if cp in (0xFE0F, 0x200D):
            continue
        if cp > 0x2000 and not (0x0980 <= cp <= 0x09FF):
            hex_cp = f"{cp:x}"
            parts.append(
                f'<img class="emoji-icon" src="https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/svg/{hex_cp}.svg" '
                f'alt="{html.escape(ch)}" onerror="this.replaceWith(document.createTextNode(this.alt))" />'
            )
        else:
            parts.append(html.escape(ch))
    return "".join(parts)


def build_viral_thumbnail_html(bg_data_uri, hook_data, width, height, theme):
    is_portrait = height >= width
    badge_esc = render_badge_with_twemoji(hook_data["badge"])
    line1_esc = html.escape(hook_data["line1"])
    line2_esc = html.escape(hook_data["line2"])

    len1 = len(hook_data["line1"])
    len2 = len(hook_data["line2"])

    if is_portrait:
        l1_size = 112 if len1 <= 12 else (96 if len1 <= 16 else (82 if len1 <= 21 else 68))
        l2_size = 102 if len2 <= 13 else (88 if len2 <= 17 else (74 if len2 <= 22 else 62))
        badge_size = 46
        stroke_px = 14
        layout_css = f"""
        .hook-wrap {{
            position: absolute;
            left: 40px;
            right: 40px;
            bottom: 175px;
            display: flex;
            flex-direction: column;
            align-items: center;
            text-align: center;
            z-index: 10;
        }}
        .scrim {{
            position: absolute;
            inset: 0;
            background:
                radial-gradient(ellipse at 50% 86%, {theme['accent_glow']} 0%, transparent 48%),
                linear-gradient(180deg,
                    rgba(0,0,0,0.00) 0%,
                    rgba(0,0,0,0.00) 60%,
                    rgba(4,6,14,0.72) 69%,
                    rgba(3,4,10,0.95) 78%,
                    rgba(2,3,8,0.99) 100%);
            z-index: 2;
        }}
        """
    else:
        l1_size = 80 if len1 <= 14 else (68 if len1 <= 19 else 56)
        l2_size = 76 if len2 <= 15 else (64 if len2 <= 20 else 52)
        badge_size = 35
        stroke_px = 11
        layout_css = f"""
        .hook-wrap {{
            position: absolute;
            left: 52px;
            right: 52px;
            bottom: 44px;
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            text-align: left;
            z-index: 10;
        }}
        .scrim {{
            position: absolute;
            inset: 0;
            background:
                radial-gradient(ellipse at 28% 82%, {theme['accent_glow']} 0%, transparent 50%),
                linear-gradient(180deg,
                    rgba(0,0,0,0.12) 0%,
                    rgba(0,0,0,0.00) 36%,
                    rgba(4,6,14,0.78) 68%,
                    rgba(2,3,8,0.96) 100%);
            z-index: 2;
        }}
        """

    return f"""<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Anek+Bangla:wght@800&family=Baloo+Da+2:wght@800&family=Hind+Siliguri:wght@700&family=Noto+Color+Emoji&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: {width}px;
    height: {height}px;
    overflow: hidden;
    background: #05070e;
    font-family: 'Baloo Da 2', 'Anek Bangla', 'Hind Siliguri', 'Noto Sans Bengali', 'Noto Color Emoji', sans-serif;
  }}
  .canvas {{
    position: relative;
    width: {width}px;
    height: {height}px;
    overflow: hidden;
  }}
  .bg-img {{
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: center center;
    filter: contrast(1.08) saturate(1.16);
    z-index: 1;
  }}
  .frame {{
    position: absolute;
    inset: 0;
    box-shadow: inset 0 0 110px rgba(0,0,0,0.55);
    border: 8px solid {theme['frame_border']};
    z-index: 4;
    pointer-events: none;
  }}
  {layout_css}
  .badge {{
    display: inline-flex;
    align-items: center;
    gap: 12px;
    background: {theme['badge_bg']};
    color: {theme['badge_text']};
    font-size: {badge_size}px;
    font-weight: 800;
    line-height: 1.15;
    padding: 12px 36px 10px 36px;
    border-radius: 999px;
    border: 4px solid {theme['badge_border']};
    box-shadow: 0 10px 30px {theme['badge_glow']}, 0 6px 0 #000000;
    transform: rotate(-2deg);
    margin-bottom: 20px;
  }}
  .emoji-icon {{
    width: {badge_size + 2}px;
    height: {badge_size + 2}px;
    flex-shrink: 0;
    filter: drop-shadow(0 2px 4px rgba(0,0,0,0.45));
  }}
  .line1 {{
    color: #FFFFFF;
    font-size: {l1_size}px;
    font-weight: 800;
    line-height: 1.14;
    paint-order: stroke fill;
    -webkit-text-stroke: {stroke_px}px #000000;
    text-shadow: 0 6px 0 #000000, 0 12px 24px rgba(0,0,0,0.95);
    margin-bottom: 16px;
    max-width: 100%;
  }}
  .tape {{
    display: inline-block;
    background: {theme['tape_bg']};
    border: 5px solid {theme['tape_border']};
    border-radius: 22px;
    padding: 12px 36px 8px 36px;
    box-shadow: 0 12px 0 #000000, 0 20px 42px rgba(0,0,0,0.92);
    transform: rotate(-1.4deg);
    max-width: 100%;
  }}
  .line2 {{
    color: {theme['tape_text']};
    font-size: {l2_size}px;
    font-weight: 800;
    line-height: 1.14;
    paint-order: stroke fill;
    -webkit-text-stroke: {theme['tape_stroke']};
  }}
</style>
</head>
<body>
  <div class="canvas">
    <img class="bg-img" src="{bg_data_uri}" alt="" />
    <div class="scrim"></div>
    <div class="frame"></div>
    <div class="hook-wrap">
      <div class="badge">{badge_esc}</div>
      <div class="line1">{line1_esc}</div>
      <div class="tape">
        <div class="line2">{line2_esc}</div>
      </div>
    </div>
  </div>
</body>
</html>"""


def save_optimized_jpeg(img, dest_path, max_bytes=4500000):
    rgb = img.convert("RGB")
    for quality in (93, 88, 82, 75):
        rgb.save(dest_path, "JPEG", quality=quality, subsampling=0, optimize=True)
        if os.path.getsize(dest_path) <= max_bytes:
            return
    rgb.save(dest_path, "JPEG", quality=70, optimize=True)


def generate_viral_thumbnail(video_path, dest_thumb_path, hook_data):
    specs = inspect_video_specs(video_path)
    if specs is not None:
        vid_w, vid_h, duration = specs
    else:
        vid_w, vid_h, duration = 1080, 1920, 30.0

    mode = FB_UPLOAD_MODE.upper().strip()
    is_portrait = True if mode == "REELS" else (vid_h >= vid_w)
    target_w, target_h = (1080, 1920) if is_portrait else (1280, 720)
    theme = random.choice(VIRAL_THUMB_THEMES)

    work_dir = tempfile.mkdtemp(prefix="fb_thumb_")
    try:
        best_frame = extract_best_video_frame(video_path, duration, work_dir)
        if not best_frame:
            fallback_img = Image.new("RGB", (target_w, target_h), (18, 24, 42))
            best_frame = os.path.join(work_dir, "fallback.jpg")
            fallback_img.save(best_frame, "JPEG", quality=90)

        with open(best_frame, "rb") as bf:
            data_uri = "data:image/jpeg;base64," + base64.b64encode(bf.read()).decode("ascii")

        browser = find_headless_browser()
        if browser:
            html_path = os.path.join(work_dir, "thumb.html")
            png_path = os.path.join(work_dir, "thumb.png")
            html_content = build_viral_thumbnail_html(data_uri, hook_data, target_w, target_h, theme)
            with open(html_path, "w", encoding="utf-8") as hf:
                hf.write(html_content)

            file_url = "file:///" + os.path.abspath(html_path).replace("\\", "/")
            cmd = [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                f"--window-size={target_w},{target_h}",
                "--virtual-time-budget=4000",
                f"--screenshot={png_path}",
                file_url,
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=40)
            if os.path.exists(png_path) and os.path.getsize(png_path) > 5000:
                with Image.open(png_path) as rendered:
                    cropped = rendered.convert("RGB").crop((0, 0, target_w, target_h))
                    save_optimized_jpeg(cropped, dest_thumb_path)
                print(
                    f"Viral hook thumbnail generated ({target_w}x{target_h}, theme={theme['name']}, "
                    f"size={os.path.getsize(dest_thumb_path) // 1024}KB)."
                )
                return True

        with Image.open(best_frame) as im:
            im = im.convert("RGB").resize((target_w, target_h), Image.Resampling.LANCZOS)
            im = ImageEnhance.Contrast(im).enhance(1.18)
            im = ImageEnhance.Color(im).enhance(1.25)
            save_optimized_jpeg(im, dest_thumb_path)
        print("Fallback enhanced frame thumbnail generated.")
        return True
    except Exception as e:
        print(f"[Warning] Thumbnail generation failed: {e}")
        return False
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def set_facebook_thumbnail(video_id, thumb_path):
    """Uploads custom cover thumbnail to a published Facebook Reel or Page Video via /{video_id}/thumbnails."""
    if not video_id or not thumb_path or not os.path.exists(thumb_path):
        return False

    thumb_url = f"https://graph.facebook.com/{FB_API_VERSION}/{video_id}/thumbnails"
    for attempt in range(1, 5):
        try:
            with open(thumb_path, "rb") as tf:
                res = requests.post(
                    thumb_url,
                    data={
                        "access_token": FB_PAGE_ACCESS_TOKEN,
                        "is_preferred": "true",
                    },
                    files={"source": ("thumbnail.jpg", tf, "image/jpeg")},
                    timeout=45,
                )
            if res.status_code == 200 and res.json().get("success"):
                print(f"Custom viral thumbnail applied to Facebook video/Reel {video_id}!")
                return True
            print(f"[Thumbnail attempt {attempt}/4] Facebook response ({res.status_code}): {res.text[:220]}")
        except Exception as e:
            print(f"[Thumbnail attempt {attempt}/4] Exception: {e}")
        if attempt < 4:
            time.sleep(6)

    print(f"[Warning] Could not confirm custom thumbnail via /thumbnails for {video_id}.")
    return False


def upload_facebook_reel(video_path, description, thumb_path=None):
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

    status_url = f"https://graph.facebook.com/{FB_API_VERSION}/{video_id}"
    for _ in range(8):
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
    if thumb_path and os.path.exists(thumb_path):
        set_facebook_thumbnail(video_id, thumb_path)
    return video_id


def upload_facebook_page_video(video_path, title, description, thumb_path=None):
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

    finish_files = None
    thumb_fh = None
    try:
        if thumb_path and os.path.exists(thumb_path):
            thumb_fh = open(thumb_path, "rb")
            finish_files = {"thumb": ("thumbnail.jpg", thumb_fh, "image/jpeg")}

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
                files=finish_files,
                timeout=60,
            ),
            "Page video finish",
        )
    finally:
        if thumb_fh:
            thumb_fh.close()

    if not finish_res.get("success"):
        raise RuntimeError(f"Page video finish failed: {finish_res}")

    print(f"Published as Facebook Page Video! Video ID: {video_id}")
    if video_id and thumb_path and os.path.exists(thumb_path) and not finish_files:
        set_facebook_thumbnail(video_id, thumb_path)
    return video_id


def upload_to_facebook(video_path, title, description, attribution="", thumb_path=None):
    mode = FB_UPLOAD_MODE.upper().strip()
    reel_caption = build_reel_caption(title, description, attribution)
    page_description = format_facebook_caption(description, attribution, max_len=PAGE_DESCRIPTION_MAX_LEN)

    if mode == "REELS":
        return upload_facebook_reel(video_path, reel_caption, thumb_path=thumb_path)
    if mode == "VIDEO":
        return upload_facebook_page_video(video_path, title, page_description, thumb_path=thumb_path)

    specs = inspect_video_specs(video_path)
    if specs is not None:
        width, height, duration = specs
        print(f"Detected video specs: {width}x{height}, duration={duration:.1f}s")
        if width > height or duration < 3.0 or duration > 90.0:
            print("Video is horizontal or outside 3-90s Reel limit. Uploading directly as Facebook Page Video...")
            return upload_facebook_page_video(video_path, title, page_description, thumb_path=thumb_path)

    try:
        return upload_facebook_reel(video_path, reel_caption, thumb_path=thumb_path)
    except Exception as e:
        print(f"Reels upload skipped/failed ({e}). Falling back to regular Facebook Page Video...")
        return upload_facebook_page_video(video_path, title, page_description, thumb_path=thumb_path)


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
    thumb_path = os.path.join(tempfile.gettempdir(), "temp_fb_thumb.jpg")

    print(f"\nSelected Folder #{selected_folder['index']} ({selected_folder['folder_id']})")
    print(f"Processing: {file_name}")
    metadata = generate_facebook_metadata(file_name)

    if metadata is None:
        print("Both Gemini and Groq APIs failed. Skipping upload for this run so it retries cleanly next time.")
        return

    title, description, hook_data = metadata
    print(f"Generated Title: {title}")
    print(f"Generated Thumbnail Hook: [{hook_data['badge']}] {hook_data['line1']} -> {hook_data['line2']}")
    if attribution:
        print(f"Attribution Ready ({len(attribution)} chars)")

    try:
        download_video(drive, file_id, temp_path)
        thumb_ok = generate_viral_thumbnail(temp_path, thumb_path, hook_data)
        upload_to_facebook(
            temp_path,
            title,
            description,
            attribution,
            thumb_path=thumb_path if thumb_ok else None,
        )
    finally:
        for p in (temp_path, thumb_path):
            if os.path.exists(p):
                os.remove(p)

    uploaded_ids.append(file_id)
    save_uploaded_ids(uploaded_ids)


if __name__ == "__main__":
    main()
