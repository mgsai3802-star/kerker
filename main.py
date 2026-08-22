import os
import threading
import random
import string
import httpx
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import RedirectResponse
from supabase import create_client, Client
import zipfile
import io
from datetime import datetime

# ==========================================
# 0. Environment Variables များကို ဆွဲယူခြင်း
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

PROXY_DOMAIN = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")

if not all([BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("Environment variables များ ပြည့်စုံစွာ မပါဝင်ပါ။")

# --- Initialize ---
bot = telebot.TeleBot(BOT_TOKEN)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI()

ADMIN_CHAT_ID = 1847021130
TARGET_URL = "https://chatgpt.com"

# User များ၏ Daily Limit မှတ်သားရန် (Memory တွင် ယာယီသိမ်းမည်)
user_daily_limits = {}

def check_user_limit(user_id):
    if user_id == ADMIN_CHAT_ID:
        return True # Admin အတွက် Limit မရှိပါ
    
    today = datetime.now().strftime("%Y-%m-%d")
    user_data = user_daily_limits.get(user_id, {"date": today, "count": 0})
    
    # နေ့စွဲ ပြောင်းသွားလျှင် Limit ကို 0 ပြန်ထားမည်
    if user_data["date"] != today:
        user_data = {"date": today, "count": 0}
        
    if user_data["count"] >= 5:
        return False # ၅ ခါ ပြည့်သွားပြီ
        
    user_data["count"] += 1
    user_daily_limits[user_id] = user_data
    return True


# ==========================================
# အပိုင်း (၁) : Telegram Bot လုပ်ဆောင်ချက်များ
# ==========================================

@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    markup = InlineKeyboardMarkup(row_width=1)
    
    btn_get = InlineKeyboardButton("🔑 လင့်ယူရန်", callback_data="get_account")
    btn_vip = InlineKeyboardButton("🌟 Get VIP", callback_data="get_vip")
    
    # Admin ဆိုလျှင် ခလုတ်အကုန်ပြမည်၊ မဟုတ်လျှင် ၂ ခုသာပြမည်
    if message.chat.id == ADMIN_CHAT_ID:
        btn_stock = InlineKeyboardButton("📊 လက်ကျန်စစ်မည်", callback_data="check_stock")
        btn_zip = InlineKeyboardButton("📁 Cookie ZIP / TXT တင်မည်", callback_data="upload_zip")
        markup.add(btn_get, btn_vip, btn_stock, btn_zip)
        text_msg = "👨‍💻 Admin Menu သို့ ကြိုဆိုပါတယ်။"
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
        res = supabase.table("cookies_pool").select("id").execute()
        bot.send_message(call.message.chat.id, f"📊 Database တွင် Cookie <b>({len(res.data)})</b> ခု ကျန်ရှိပါသေးသည်။", parse_mode="HTML")
    except Exception:
        bot.send_message(call.message.chat.id, "❌ လက်ကျန်စစ်ဆေးရာတွင် အမှားဖြစ်နေပါသည်။")

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
            
        bot.edit_message_text(f"✅ အောင်မြင်ပါသည်။ Cookie အသစ် ({len(all_cookies)}) ခု ထည့်သွင်းပြီးပါပြီ။", chat_id=message.chat.id, message_id=msg.message_id)
    except Exception as e:
        bot.reply_to(message, f"❌ ဖိုင်ဖတ်ရာတွင် အမှားဖြစ်နေပါသည်: {e}")

@bot.callback_query_handler(func=lambda call: call.data == "get_account")
def handle_get_account(call):
    # Daily Limit စစ်ဆေးခြင်း
    if not check_user_limit(call.message.chat.id):
        bot.answer_callback_query(call.id, "⚠️ သင့်၏ ယနေ့အတွက် အခမဲ့ (၅) ကြိမ် ကန့်သတ်ချက် ပြည့်သွားပါပြီ။ မနက်ဖြန်မှ ထပ်မံကြိုးစားပါ။", show_alert=True)
        return

    try:
        bot.answer_callback_query(call.id, "⏳ လင့်ခ် ထုတ်ယူနေပါသည်...")
        res = supabase.table("cookies_pool").select("cookie_value").execute()
        if not res.data:
            bot.send_message(call.message.chat.id, "⚠️ လက်ရှိတွင် အသုံးပြုနိုင်သော Cookie မရှိသေးပါ။")
            return
            
        selected_cookie = random.choice(res.data)['cookie_value']
        token = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
        supabase.table("sessions").insert({"token": token, "cookie_value": selected_cookie}).execute()
        
        clean_domain = PROXY_DOMAIN.rstrip("/")
        magic_link = f"{clean_domain}/login?token={token}"
        
        text = (
            "🎉 သင့်အတွက် လင့်ခ် အဆင်သင့်ဖြစ်ပါပြီ။\n\n"
            "အောက်ပါ Link ကို နှိပ်၍ <b>Chrome Browser</b> ဖြင့် ဖွင့်ပါ:\n"
            f"{magic_link}\n\n"
            "<i>(ယနေ့ အခမဲ့ရယူခွင့် ၅ ကြိမ်တွင် ၁ ကြိမ် ခုနှိမ်လိုက်ပါသည်)</i>"
        )
        bot.send_message(call.message.chat.id, text, parse_mode="HTML")
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ လင့်ခ်ထုတ်ပေးရာတွင် အခက်အခဲဖြစ်နေပါသည်။ Error: {e}")

# (အသစ်) စာသား (Cookie Text) တိုက်ရိုက်ပို့လျှင် Link ပြောင်းပေးမည့်စနစ်
@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_text_cookie(message):
    text = message.text.strip()
    # Command များကို ကျော်သွားရန်
    if text.startswith('/'):
        return
        
    try:
        msg = bot.reply_to(message, "⏳ သင့် Cookie အား Proxy Link အဖြစ် ပြောင်းလဲနေပါသည်...")
        token = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
        
        # User ပေးသော Cookie အား Sessions ထဲသို့ တိုက်ရိုက်ထည့်မည်
        supabase.table("sessions").insert({"token": token, "cookie_value": text}).execute()
        
        clean_domain = PROXY_DOMAIN.rstrip("/")
        magic_link = f"{clean_domain}/login?token={token}"
        
        reply_text = (
            "✅ <b>အောင်မြင်ပါသည်။</b>\n\n"
            "သင့် Cookie အား လင့်ခ်အဖြစ် ပြောင်းလဲပြီးပါပြီ။ အောက်ပါ Link ကို နှိပ်၍ Chrome ဖြင့် ဖွင့်ပါ:\n"
            f"{magic_link}"
        )
        bot.edit_message_text(reply_text, chat_id=message.chat.id, message_id=msg.message_id, parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ Link ပြောင်းရာတွင် အမှားဖြစ်နေပါသည်: {e}")


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
# အပိုင်း (၂) : FastAPI Reverse Proxy
# ==========================================

@app.get("/ping")
def health_check():
    return {"status": "alive", "message": "Proxy and Bot are running"}

@app.get("/login")
async def login_and_set_cookie(token: str):
    try:
        response = supabase.table("sessions").select("cookie_value").eq("token", token).execute()
        if not response.data:
            return {"Error": "Token အမှား (သို့) သက်တမ်းကုန်သွားပါပြီ။"}
            
        actual_cookie = response.data[0]["cookie_value"]
        redirect = RedirectResponse(url="/")
        redirect.set_cookie(
            key="__Secure-next-auth.session-token",
            value=actual_cookie,
            httponly=True,
            secure=True,
            samesite="lax"
        )
        return redirect
    except Exception as e:
        return {"System_Error": str(e), "Error_Type": str(type(e))}

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def reverse_proxy(request: Request, path: str):
    async with httpx.AsyncClient(base_url=TARGET_URL, follow_redirects=False) as client:
        url = httpx.URL(path=request.url.path, query=request.url.query.encode("utf-8"))
        
        headers = dict(request.headers)
        headers["host"] = "chatgpt.com"
        if "origin" in headers:
            headers["origin"] = TARGET_URL
        if "referer" in headers:
            headers["referer"] = headers["referer"].replace(str(request.base_url), TARGET_URL + "/")
            
        req = client.build_request(request.method, url, headers=headers, content=await request.body())
        
        try:
            httpx_resp = await client.send(req, stream=True)
            res_headers = dict(httpx_resp.headers)
            
            for h in ["content-encoding", "content-length", "transfer-encoding"]:
                res_headers.pop(h, None)
                
            return Response(
                content=await httpx_resp.aread(),
                status_code=httpx_resp.status_code,
                headers=res_headers
            )
        except Exception as e:
            return Response(content=f"Proxy Fetch Error: {str(e)}", status_code=500)
