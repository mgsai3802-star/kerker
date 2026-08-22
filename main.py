import os
import threading
import random
import string
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import RedirectResponse
from supabase import create_client, Client
import zipfile
import io
import re
from datetime import datetime

# Cloudflare ကို ကျော်ဖြတ်ပေးမည့် Chrome ဟန်ဆောင် Library
from curl_cffi.requests import AsyncSession

# ==========================================
# 0. Environment Variables များကို ဆွဲယူခြင်း
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
PROXY_DOMAIN = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")

if not all([BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("Environment variables များ ပြည့်စုံစွာ မပါဝင်ပါ။")

bot = telebot.TeleBot(BOT_TOKEN)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI()

ADMIN_CHAT_ID = 1847021130
TARGET_URL = "https://chatgpt.com"

# ==========================================
# User & VIP/Ban Management System
# ==========================================
def get_or_create_user(user_id):
    if user_id == ADMIN_CHAT_ID:
        return {"user_id": user_id, "is_vip": True, "is_banned": False, "usage_date": "", "usage_count": 0}
        
    res = supabase.table("users").select("*").eq("user_id", user_id).execute()
    if res.data:
        return res.data[0]
    
    new_user = {
        "user_id": user_id,
        "is_vip": False,
        "is_banned": False,
        "usage_date": datetime.now().strftime("%Y-%m-%d"),
        "usage_count": 0
    }
    supabase.table("users").insert(new_user).execute()
    return new_user

def check_user_access(user_id):
    if user_id == ADMIN_CHAT_ID:
        return True, "admin"
        
    user = get_or_create_user(user_id)
    
    if user["is_banned"]:
        return False, "banned"
        
    if user["is_vip"]:
        return True, "vip"
        
    today = datetime.now().strftime("%Y-%m-%d")
    if user["usage_date"] != today:
        supabase.table("users").update({"usage_date": today, "usage_count": 1}).eq("user_id", user_id).execute()
        return True, "normal"
    else:
        if user["usage_count"] >= 5:
            return False, "limit"
        else:
            supabase.table("users").update({"usage_count": user["usage_count"] + 1}).eq("user_id", user_id).execute()
            return True, "normal"

# ==========================================
# အပိုင်း (၁) : Telegram Bot လုပ်ဆောင်ချက်များ
# ==========================================

@bot.message_handler(commands=['ban', 'unban', 'addvip', 'rmvip'])
def manage_users(message):
    if message.chat.id != ADMIN_CHAT_ID:
        return
        
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "အသုံးပြုနည်း: /ban [user_id]")
        return
        
    try:
        target_id = int(parts[1])
        get_or_create_user(target_id)
        
        cmd = parts[0].lower()
        if cmd == '/ban':
            supabase.table("users").update({"is_banned": True}).eq("user_id", target_id).execute()
            bot.reply_to(message, f"✅ User {target_id} ကို Ban လိုက်ပါပြီ။")
        elif cmd == '/unban':
            supabase.table("users").update({"is_banned": False}).eq("user_id", target_id).execute()
            bot.reply_to(message, f"✅ User {target_id} ကို Ban ဖြုတ်ပေးလိုက်ပါပြီ။")
        elif cmd == '/addvip':
            supabase.table("users").update({"is_vip": True}).eq("user_id", target_id).execute()
            bot.reply_to(message, f"✅ User {target_id} ကို VIP ပြောင်းပေးလိုက်ပါပြီ။")
        elif cmd == '/rmvip':
            supabase.table("users").update({"is_vip": False}).eq("user_id", target_id).execute()
            bot.reply_to(message, f"✅ User {target_id} ကို VIP မှ ဖယ်ရှားလိုက်ပါပြီ။")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    markup = InlineKeyboardMarkup(row_width=1)
    btn_get = InlineKeyboardButton("🔑 လင့်ယူရန်", callback_data="get_account")
    btn_vip = InlineKeyboardButton("🌟 Get VIP", callback_data="get_vip")
    
    if message.chat.id == ADMIN_CHAT_ID:
        btn_stock = InlineKeyboardButton("📊 လက်ကျန်စစ်မည်", callback_data="check_stock")
        btn_zip = InlineKeyboardButton("📁 Cookie ZIP / TXT တင်မည်", callback_data="upload_zip")
        markup.add(btn_get, btn_vip, btn_stock, btn_zip)
        text_msg = "👨‍💻 Admin Menu သို့ ကြိုဆိုပါတယ်။\n(User များကို စီမံရန် /ban, /unban, /addvip, /rmvip [user_id] ကိုသုံးပါ)"
    else:
        markup.add(btn_get, btn_vip)
        text_msg = "မင်္ဂလာပါ။ ChatGPT လင့်ခ်ရယူရန် အောက်ပါ ခလုတ်ကို နှိပ်ပါ။\n*(တစ်နေ့လျှင် ၅ ကြိမ် အခမဲ့ ရယူနိုင်ပါသည်)*"
        
    bot.reply_to(message, text_msg, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "get_vip")
def handle_get_vip(call):
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "🌟 VIP အကောင့် ဝယ်ယူရန် သို့မဟုတ် Limit အကန့်အသတ်မရှိ သုံးရန် Admin ကို ဆက်သွယ်ပါ။")

@bot.callback_query_handler(func=lambda call: call.data == "check_stock")
def handle_check_stock(call):
    if call.message.chat.id != ADMIN_CHAT_ID:
        return
    try:
        bot.answer_callback_query(call.id, "📊 လက်ကျန် စစ်ဆေးနေပါသည်...")
        res = supabase.table("cookies_pool").select("id", count="exact").execute()
        bot.send_message(call.message.chat.id, f"📊 Database တွင် Cookie <b>({res.count})</b> ခု ကျန်ရှိပါသေးသည်။", parse_mode="HTML")
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ Error: {e}")

@bot.callback_query_handler(func=lambda call: call.data == "upload_zip")
def prompt_zip_upload(call):
    if call.message.chat.id != ADMIN_CHAT_ID:
        return
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "📁 Cookie တွေပါတဲ့ `.zip` (သို့) `.txt` ဖိုင်ကို တိုက်ရိုက် ပို့ပေးပါ။")

@bot.message_handler(content_types=['document'])
def handle_document(message):
    if message.chat.id != ADMIN_CHAT_ID:
        return
    try:
        file_name = message.document.file_name
        if not (file_name.endswith('.zip') or file_name.endswith('.txt')):
            bot.reply_to(message, "⚠️ `.zip` သို့မဟုတ် `.txt` ဖိုင်ကိုသာ တင်ပေးပါ။")
            return
        
        msg = bot.reply_to(message, "⏳ Database သို့ ထည့်သွင်းနေပါပြီ...")
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        all_cookies = []
        if file_name.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(downloaded_file)) as z:
                for name in z.namelist():
                    if name.endswith('.txt'):
                        content = z.read(name).decode('utf-8', errors='ignore') 
                        all_cookies.extend([line.strip() for line in content.split('\n') if line.strip()])
        elif file_name.endswith('.txt'):
            content = downloaded_file.decode('utf-8', errors='ignore')
            all_cookies.extend([line.strip() for line in content.split('\n') if line.strip()])
            
        if not all_cookies:
            bot.edit_message_text("⚠️ Cookie စာသားများ မတွေ့ရှိပါ။", chat_id=message.chat.id, message_id=msg.message_id)
            return

        insert_data = [{"cookie_value": c} for c in all_cookies]
        for i in range(0, len(insert_data), 500):
            supabase.table("cookies_pool").insert(insert_data[i:i + 500]).execute()
            
        bot.edit_message_text(f"✅ အောင်မြင်ပါသည်။ Cookie အသစ် ({len(all_cookies)}) ခု ထည့်သွင်းပြီးပါပြီ。", chat_id=message.chat.id, message_id=msg.message_id)
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

@bot.callback_query_handler(func=lambda call: call.data == "get_account")
def handle_get_account(call):
    status, role = check_user_access(call.message.chat.id)
    if status == False:
        if role == "banned":
            bot.answer_callback_query(call.id, "🚫 သင့်အကောင့်အား ပိတ်ပင် (Ban) ထားပါသည်။", show_alert=True)
        elif role == "limit":
            bot.answer_callback_query(call.id, "⚠️ သင့်၏ ယနေ့အတွက် အခမဲ့ (၅) ကြိမ် ကန့်သတ်ချက် ပြည့်သွားပါပြီ။ မနက်ဖြန်မှ ထပ်မံကြိုးစားပါ။", show_alert=True)
        return

    try:
        bot.answer_callback_query(call.id, "⏳ လင့်ခ် ထုတ်ယူနေပါသည်...")
        res = supabase.table("cookies_pool").select("id, cookie_value").execute()
        if not res.data:
            bot.send_message(call.message.chat.id, "⚠️ လက်ရှိတွင် အသုံးပြုနိုင်သော Cookie မရှိသေးပါ။ (Stock ကုန်နေပါသည်)")
            return
            
        selected_record = random.choice(res.data)
        cookie_id = selected_record['id']
        selected_cookie = selected_record['cookie_value']
        
        # ⚠️ အကောင့်ထုတ်ပေးပြီးပါက Database မှ ပြန်ဖျက်ပစ်မည် (Stock လျော့သွားမည်)
        supabase.table("cookies_pool").delete().eq("id", cookie_id).execute()
        
        token = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
        supabase.table("sessions").insert({"token": token, "cookie_value": selected_cookie}).execute()
        
        clean_domain = PROXY_DOMAIN.rstrip("/")
        magic_link = f"{clean_domain}/login?token={token}"
        
        text = (
            "🎉 <b>သင့်အတွက် လင့်ခ် အဆင်သင့်ဖြစ်ပါပြီ။</b>\n\n"
            "အောက်ပါ Link ကို နှိပ်၍ <b>Chrome Browser</b> ဖြင့် ဖွင့်ပါ:\n"
            f"{magic_link}\n\n"
        )
        if role == "normal":
            text += "<i>(ယနေ့ အခမဲ့ရယူခွင့် ၅ ကြိမ်တွင် ၁ ကြိမ် ခုနှိမ်လိုက်ပါသည်)</i>"
        elif role == "vip":
            text += "<i>(👑 VIP အကောင့်ဖြစ်သဖြင့် အကန့်အသတ်မရှိ ရယူနိုင်ပါသည်)</i>"
            
        bot.send_message(call.message.chat.id, text, parse_mode="HTML")
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ Error: {e}")

# --- Background Bot Run ---
def run_bot():
    print("Telegram Bot နောက်ကွယ်မှ စတင် အလုပ်လုပ်နေပါပြီ...")
    try:
        bot.remove_webhook()
        bot.polling(none_stop=True, timeout=60)
    except Exception as e:
        print(f"Bot Polling Error: {e}")

@app.on_event("startup")
def on_startup():
    threading.Thread(target=run_bot, daemon=True).start()

# ==========================================
# အပိုင်း (၂) : FastAPI Reverse Proxy (Cloudflare Bypass ဖြင့်)
# ==========================================

@app.get("/ping")
def health_check():
    return {"status": "alive"}

@app.get("/login")
async def login_and_set_cookie(token: str):
    try:
        response = supabase.table("sessions").select("cookie_value").eq("token", token).execute()
        if not response.data:
            return {"Error": "Token အမှား (သို့) သက်တမ်းကုန်သွားပါပြီ။"}
            
        raw_cookie = response.data[0]["cookie_value"].strip()
        # Cookie အမှိုက်များ ရှင်းလင်းခြင်း (Tab များပါနေပါက ဖြတ်ထုတ်မည်)
        clean_cookie = re.split(r'[\t\s]+', raw_cookie)[-1]
        
        redirect = RedirectResponse(url="/")
        redirect.set_cookie(
            key="__Secure-next-auth.session-token",
            value=clean_cookie,
            httponly=True,
            secure=True,
            samesite="lax"
        )
        return redirect
    except Exception as e:
        return {"System_Error": str(e), "Error_Type": str(type(e))}

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def reverse_proxy(request: Request, path: str):
    url = f"{TARGET_URL}/{path}"
    if request.url.query:
        url += f"?{request.url.query}"
        
    headers = dict(request.headers)
    headers["host"] = "chatgpt.com"
    
    # ဂြိုဟ်သားစာမပေါ်စေရန် zip format တောင်းဆိုမှုကို ပိတ်ခြင်း
    headers.pop("accept-encoding", None)
    
    if "origin" in headers:
        headers["origin"] = TARGET_URL
    if "referer" in headers:
        headers["referer"] = headers["referer"].replace(str(request.base_url), TARGET_URL + "/")
        
    body = await request.body()
    
    try:
        # Chrome Browser အစစ်ကဲ့သို့ ဟန်ဆောင်၍ Cloudflare ကို ကျော်ဖြတ်ခြင်း
        async with AsyncSession(impersonate="chrome120") as client:
            resp = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                data=body,
                allow_redirects=False
            )
            
        res_headers = dict(resp.headers)
        for h in ["content-encoding", "content-length", "transfer-encoding"]:
            res_headers.pop(h, None)
            
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=res_headers
        )
    except Exception as e:
        return Response(content=f"Proxy Fetch Error: {str(e)}", status_code=500)
