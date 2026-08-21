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
import zipfile  # ZIP ဖိုင် ဖြည်ရန် အသစ်ထည့်ထားသည်
import io       # Memory တွင် ဖိုင်ဖတ်ရန် အသစ်ထည့်ထားသည်

# --- Environment Variables ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
PROXY_DOMAIN = os.environ.get("PROXY_DOMAIN")

if not all([BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY, PROXY_DOMAIN]):
    raise ValueError("Environment variables များ ပြည့်စုံစွာ မပါဝင်ပါ။")

# --- Initialize ---
bot = telebot.TeleBot(BOT_TOKEN)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI()

# Admin Chat ID
ADMIN_CHAT_ID = 1847021130
TARGET_URL = "https://chatgpt.com"

# ==========================================
# အပိုင်း (၁) : Telegram Bot လုပ်ဆောင်ချက်များ
# ==========================================
@bot.message_handler(commands=['addcookie'])
def add_new_cookie(message):
    if message.chat.id != ADMIN_CHAT_ID:
        bot.reply_to(message, "သင့်တွင် လုပ်ပိုင်ခွင့် မရှိပါ။")
        return
    
    try:
        cookie_value = message.text.split(" ", 1)[1].strip()
        supabase.table("cookies_pool").insert({"cookie_value": cookie_value}).execute()
        bot.reply_to(message, "✅ Cookie အသစ်ကို Database သို့ အောင်မြင်စွာ သိမ်းဆည်းပြီးပါပြီ။")
    except IndexError:
        bot.reply_to(message, "အသုံးပြုနည်း: /addcookie [cookie_စာသား]")
    except Exception as e:
        bot.reply_to(message, f"❌ သိမ်းဆည်းရာတွင် အမှားဖြစ်နေပါသည်: {e}")

# (ပြင်ဆင်ထားသော Menu)
@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    markup = InlineKeyboardMarkup()
    btn_get = InlineKeyboardButton("🔑 အကောင့်ယူမည်", callback_data="get_account")
    markup.add(btn_get)
    
    # Admin ဖြစ်လျှင် ZIP တင်ရန် ခလုတ်ပါ ပြပေးမည်
    if message.chat.id == ADMIN_CHAT_ID:
        btn_zip = InlineKeyboardButton("📁 Cookie ZIP / TXT တင်မည်", callback_data="upload_zip")
        markup.add(btn_zip)
        
    bot.reply_to(message, "မင်္ဂလာပါ။ ChatGPT အကောင့် ယူရန် အောက်ပါ ခလုတ်ကို နှိပ်ပါ။", reply_markup=markup)

# (ZIP ခလုတ်ကို နှိပ်လျှင် အလုပ်လုပ်မည့် အပိုင်း)
@bot.callback_query_handler(func=lambda call: call.data == "upload_zip")
def prompt_zip_upload(call):
    if call.message.chat.id != ADMIN_CHAT_ID:
        bot.answer_callback_query(call.id, "သင့်တွင် လုပ်ပိုင်ခွင့် မရှိပါ။", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "📁 သင့်မှာရှိတဲ့ Cookie တွေပါတဲ့ `.zip` (သို့) `.txt` ဖိုင်ကို ဒီထဲသို့ တိုက်ရိုက် ပို့ပေးပါ။\n\n*(ဖိုင်ထဲရှိ စာကြောင်းတစ်ကြောင်းကို Cookie တစ်ခုအဖြစ် မှတ်ယူပါမည်)*")

# (ဖိုင် (Document) ပို့လိုက်လျှင် လက်ခံမည့် အပိုင်း သစ်)
@bot.message_handler(content_types=['document'])
def handle_document(message):
    if message.chat.id != ADMIN_CHAT_ID:
        return
    
    try:
        file_name = message.document.file_name
        if not (file_name.endswith('.zip') or file_name.endswith('.txt')):
            bot.reply_to(message, "⚠️ ကျေးဇူးပြု၍ `.zip` သို့မဟုတ် `.txt` ဖိုင်ကိုသာ တင်ပေးပါ။")
            return
        
        bot.reply_to(message, "⏳ ဖိုင်ကို ဖတ်နေပါသည်... ခဏစောင့်ပါ။")
        
        # Telegram Server မှ ဖိုင်ကို ဒေါင်းလုဒ်ဆွဲခြင်း
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        cookies_added = 0
        
        # ZIP ဖိုင်ဖြစ်လျှင်
        if file_name.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(downloaded_file)) as z:
                for name in z.namelist():
                    if name.endswith('.txt'): # ZIP ထဲမှ .txt ဖိုင်များကို ရှာဖတ်မည်
                        content = z.read(name).decode('utf-8')
                        lines = [line.strip() for line in content.split('\n') if line.strip()]
                        for cookie in lines:
                            supabase.table("cookies_pool").insert({"cookie_value": cookie}).execute()
                            cookies_added += 1
                            
        # TXT ဖိုင်ဖြစ်လျှင်
        elif file_name.endswith('.txt'):
            content = downloaded_file.decode('utf-8')
            lines = [line.strip() for line in content.split('\n') if line.strip()]
            for cookie in lines:
                supabase.table("cookies_pool").insert({"cookie_value": cookie}).execute()
                cookies_added += 1
                
        if cookies_added > 0:
            bot.reply_to(message, f"✅ အောင်မြင်ပါသည်။ Cookie အသစ် ({cookies_added}) ခုကို Database သို့ ထည့်သွင်းပြီးပါပြီ။")
        else:
            bot.reply_to(message, "⚠️ ဖိုင်ထဲတွင် Cookie စာသားများ မတွေ့ရှိပါ။")
            
    except Exception as e:
        bot.reply_to(message, f"❌ ဖိုင်ဖတ်ရာတွင် အမှားဖြစ်နေပါသည်: {e}")

# (အကောင့်ယူမည် ခလုတ် နှိပ်လျှင် အလုပ်လုပ်မည့် အပိုင်း - မူလအတိုင်း)
@bot.callback_query_handler(func=lambda call: call.data == "get_account")
def handle_get_account(call):
    try:
        res = supabase.table("cookies_pool").select("cookie_value").execute()
        cookies_list = res.data
        
        if not cookies_list:
            bot.answer_callback_query(call.id, "⚠️ လက်ရှိတွင် အသုံးပြုနိုင်သော Cookie မရှိသေးပါ။", show_alert=True)
            return
            
        selected_cookie = random.choice(cookies_list)['cookie_value']
        token = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
        
        supabase.table("sessions").insert({"token": token, "cookie_value": selected_cookie}).execute()
        
        clean_domain = PROXY_DOMAIN.rstrip("/")
        magic_link = f"{clean_domain}/login?token={token}"
        
        text = (
            "🎉 သင့်အတွက် အကောင့် အဆင်သင့်ဖြစ်ပါပြီ။\n\n"
            "အောက်ပါ Link ကို နှိပ်၍ **Chrome** ဖြင့် ဖွင့်ပြီး အသုံးပြုနိုင်ပါပြီ:\n"
            f"{magic_link}\n\n"
            "*(လုံခြုံရေးအရ ဤ Link ကို အခြားသူများအား မျှဝေခြင်း မပြုပါနှင့်)*"
        )
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
        
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ အမှားအယွင်း ဖြစ်ပေါ်နေပါသည်: {e}", show_alert=True)

# --- Background Bot Run ---
def run_bot():
    print("Telegram Bot နောက်ကွယ်မှ စတင် အလုပ်လုပ်နေပါပြီ...")
    bot.polling(none_stop=True)

@app.on_event("startup")
def on_startup():
    threading.Thread(target=run_bot, daemon=True).start()

# ==========================================
# အပိုင်း (၂) : FastAPI Reverse Proxy (မူလအတိုင်း)
# ==========================================
@app.get("/ping")
def health_check():
    return {"status": "alive", "message": "Proxy and Bot are running"}

@app.get("/login")
async def login_and_set_cookie(token: str):
    response = supabase.table("sessions").select("cookie_value").eq("token", token).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Token အမှား (သို့) သက်တမ်းကုန်သွားပါပြီ။")
        
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
            raise HTTPException(status_code=500, detail=f"Proxy Error: {str(e)}")
