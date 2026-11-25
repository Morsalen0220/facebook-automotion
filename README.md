# Facebook Page & Reels Auto Publisher (Google Drive -> Facebook)

এই ফোল্ডারের সবগুলো ফাইল নতুন একটি GitHub Repository-তে আপলোড করলেই আপনার **Facebook অটো-পাবলিশ সিস্টেম** চালু হয়ে যাবে।

## ১. GitHub Secrets-এ যা যা যোগ করবেন (`Settings -> Secrets and variables -> Actions`)

| Secret Name | বিবরণ |
| :--- | :--- |
| `GOOGLE_CLIENT_ID` | আপনার Google Cloud OAuth Client ID |
| `GOOGLE_CLIENT_SECRET` | আপনার Google Cloud OAuth Client Secret |
| `GOOGLE_REFRESH_TOKEN` | Google Drive রিড করার Refresh Token |
| `FB_PAGE_ID` | আপনার Facebook Page-এর Numeric ID |
| `FB_PAGE_ACCESS_TOKEN` | আপনার Facebook Page-এর Long-Lived / Never-Expiring Page Access Token (`pages_manage_posts`, `pages_read_engagement`, `pages_show_list` পারমিশনসহ) |
| `GEMINI_API_KEY` | ফ্রি Google AI Studio (Gemini) API Key |
| `GROQ_API_KEY` | ফ্রি Groq API Key (ব্যাকআপ AI হিসেবে) |

## ২. ফোল্ডার ও মোড কনফিগারেশন (`upload.py`)
- **`FB_UPLOAD_MODE` (লাইন ২৬):**
  - `"AUTO"` (ডিফল্ট): প্রথমে **Facebook Reels** হিসেবে আপলোড করার চেষ্টা করবে; ভিডিওটি ৯০ সেকেন্ডের বড় বা আনুভূমিক হলে স্বয়ংক্রিয়ভাবে সাধারণ **Facebook Page Video** হিসেবে পাবলিশ করবে।
  - `"REELS"`: শুধুমাত্র Reels হিসেবে পাবলিশ করবে।
  - `"VIDEO"`: সরাসরি Page Video হিসেবে পাবলিশ করবে।
- **`DRIVE_FOLDERS` (লাইন ৩৪):** YouTube প্রজেক্টের মতোই কমা (`,`) দিয়ে যত খুশি Google Drive ফোল্ডার ID/URL এবং প্রতিটির আলাদা `"attribution"` বসাতে পারবেন।
