import os
import threading
import random
import re
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from fastapi import FastAPI
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

if not all([BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("Environment variables များ ပြည့်စုံစွာ မပါဝင်ပါ။")

# --- Initialize ---
bot = telebot.TeleBot(BOT_TOKEN)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI() # Render အိပ်မသွားစေရန် Dummy Server သက်သက်သာ

ADMIN_CHAT_ID = 1847021130

# 🌟 ဤနေရာတွင် Third-Party Proxy လင့်ခ်ကို ထည့်ပါ 🌟
# (အကယ်၍ ဤလင့်ခ် ပိတ်သွားပါက အခြားအလုပ်လုပ်သော Reverse Proxy လင့်ခ်ဖြင့် လဲလှယ်နိုင်ပါသည်)
THIRD_PARTY_PROXY = "https://free.chatgpt.org.uk"


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
    if user["is_banned"]: return False, "banned"
    if user["is_vip"]: return True, "vip"
        
    today = datetime.now().strftime("%Y-%m-%d")
    if user["usage_date"] != today:
        supabase.table("users").update({"usage_date": today, "usage_count": 1}).eq("user_id", user_id).execute()
        return True, "normal"
    else:
        if user["usage_count"] >= 5: return False, "limit"
        else:
            supabase.table("users").update({"usage_count": user["usage_count"] + 1}).eq("user_id", user_id).execute()
            return True, "normal"


# ==========================================
# အပိုင်း (၁) : Telegram Bot လုပ်ဆောင်ချက်များ
# ==========================================

@bot.message_handler(commands=['ban', 'unban', 'addvip', 'rmvip'])
def manage_users(message):
    if message.chat.id != ADMIN_CHAT_ID: return
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
    if call.message.chat.id != ADMIN_CHAT_ID: return
    try:
        bot.answer_callback_query(call.id, "📊 လက်ကျန် စစ်ဆေးနေပါသည်...")
        res = supabase.table("cookies_pool").select("id", count="exact").execute()
        bot.send_message(call.message.chat.id, f"📊 Database တွင် Cookie <b>({res.count})</b> ခု ကျန်ရှိပါသေးသည်။", parse_mode="HTML")
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ Error: {e}")

@bot.callback_query_handler(func=lambda call: call.data == "upload_zip")
def prompt_zip_upload(call):
    if call.message.chat.id != ADMIN_CHAT_ID: return
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "📁 Cookie တွေပါတဲ့ `.zip` (သို့) `.txt` ဖိုင်ကို တိုက်ရိုက် ပို့ပေးပါ။")

@bot.message_handler(content_types=['document'])
def handle_document(message):
    if message.chat.id != ADMIN_CHAT_ID: return
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
        if role == "banned": bot.answer_callback_query(call.id, "🚫 သင့်အကောင့်အား ပိတ်ပင် (Ban) ထားပါသည်။", show_alert=True)
        elif role == "limit": bot.answer_callback_query(call.id, "⚠️ သင့်၏ ယနေ့အတွက် အခမဲ့ (၅) ကြိမ် ကန့်သတ်ချက် ပြည့်သွားပါပြီ။ မနက်ဖြန်မှ ထပ်မံကြိုးစားပါ။", show_alert=True)
        return

    try:
        bot.answer_callback_query(call.id, "⏳ လင့်ခ် ထုတ်ယူနေပါသည်...")
        res = supabase.table("cookies_pool").select("id, cookie_value").execute()
        if not res.data:
            bot.send_message(call.message.chat.id, "⚠️ လက်ရှိတွင် အသုံးပြုနိုင်သော Cookie မရှိသေးပါ။ (Stock ကုန်နေပါသည်)")
            return
            
        selected_record = random.choice(res.data)
        cookie_id = selected_record['id']
        raw_cookie = selected_record['cookie_value'].strip()
        
        # Cookie မှ Tab များကို ဖယ်ရှား၍ သန့်စင်ခြင်း
        clean_cookie = re.split(r'[\t\s]+', raw_cookie)[-1]
        
        # Stock လျှော့ချခြင်း
        supabase.table("cookies_pool").delete().eq("id", cookie_id).execute()
        
        # Third Party Proxy ဖြင့် တိုက်ရိုက်ချိတ်ဆက်ပေးမည့် Link ဖန်တီးခြင်း
        clean_proxy = THIRD_PARTY_PROXY.rstrip("/")
        # free.chatgpt.org.uk စနစ်အရ /login?token= (သို့မဟုတ်) /api/auth/session စသည်ဖြင့် လက်ခံတတ်ပါသည်။
        # များသောအားဖြင့် Token ကို URL Parameter အနေဖြင့် တွဲပို့လေ့ရှိပါသည်။
        magic_link = f"{clean_proxy}/?__Secure-next-auth.session-token={clean_cookie}"
        
        text = f"🎉 <b>သင့်အတွက် လင့်ခ် အဆင်သင့်ဖြစ်ပါပြီ။</b>\n\nအောက်ပါ Link ကို နှိပ်၍ <b>Chrome Browser</b> ဖြင့် ဖွင့်ပါ:\n{magic_link}\n\n"
        
        if role == "normal": text += "<i>(ယနေ့ အခမဲ့ရယူခွင့် ၅ ကြိမ်တွင် ၁ ကြိမ် ခုနှိမ်လိုက်ပါသည်)</i>"
        elif role == "vip": text += "<i>(👑 VIP အကောင့်ဖြစ်သဖြင့် အကန့်အသတ်မရှိ ရယူနိုင်ပါသည်)</i>"
            
        bot.send_message(call.message.chat.id, text, parse_mode="HTML")
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ Error: {e}")

@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_text_cookie(message):
    text = message.text.strip()
    if text.startswith('/'): return
    status, role = check_user_access(message.chat.id)
    if status == False:
        if role == "banned": bot.reply_to(message, "🚫 သင့်အကောင့်အား ပိတ်ပင် (Ban) ထားပါသည်။")
        elif role == "limit": bot.reply_to(message, "⚠️ သင့်၏ ယနေ့အတွက် အခမဲ့ (၅) ကြိမ် ကန့်သတ်ချက် ပြည့်သွားပါပြီ။")
        return
        
    try:
        msg = bot.reply_to(message, "⏳ သင့် Cookie အား Proxy Link အဖြစ် ပြောင်းလဲနေပါသည်...")
        
        # သန့်စင်ခြင်း
        clean_cookie = re.split(r'[\t\s]+', text)[-1]
        
        clean_proxy = THIRD_PARTY_PROXY.rstrip("/")
        magic_link = f"{clean_proxy}/?__Secure-next-auth.session-token={clean_cookie}"
        
        reply_text = f"✅ <b>အောင်မြင်ပါသည်။</b>\n\nသင့် Cookie အား လင့်ခ်အဖြစ် ပြောင်းလဲပြီးပါပြီ။ အောက်ပါ Link ကို နှိပ်၍ Chrome ဖြင့် ဖွင့်ပါ:\n{magic_link}"
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
# အပိုင်း (၂) : FastAPI Health Check (Render အတွက်သာ)
# ==========================================
# (Proxy အပိုင်းများ ဖြုတ်ချလိုက်ပါပြီ။)

@app.get("/")
def root_check():
    return {"status": "online", "message": "Bot is running perfectly!"}

@app.get("/ping")
def health_check():
    return {"status": "alive"}
