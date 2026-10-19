import os
import gc
import time
import uuid
import queue
import urllib.request
import traceback
import subprocess
from threading import Thread, Lock
from flask import Flask
import numpy as np
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
import soundfile as sf

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN မရှိပါ! Render Environment Variables တွင် ထည့်သွင်းပေးပါ။")

bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=2)

TEMP_DIR = "temp_files"
os.makedirs(TEMP_DIR, exist_ok=True)

# --- တစ်ခုပြီးမှတစ်ခု လုပ်ဆောင်ရန် Queue နှင့် Lock စနစ် ---
job_queue = queue.Queue()
state_lock = Lock()
current_job_chat_id = None
cancel_current_job = False
cancelled_users = set()

# DeepFilterNet3 Standalone Rust AI Binary URL နှင့် လမ်းကြောင်း
DEEP_FILTER_URL = (
    "https://github.com/Rikorose/DeepFilterNet/releases/download/"
    "v0.5.6/deep-filter-0.5.6-x86_64-unknown-linux-musl"
)
DEEP_FILTER_BIN = "/usr/local/bin/deep-filter"
FALLBACK_BIN = "/tmp/deep-filter"

# Outdoor လေတိုးသံ (200Hz အောက်) ကို ဖြတ်ချပြီး အဝေးမှပြောသော လူစကားသံ (1.5kHz - 3.5kHz) ကို သီချင်းသံပေါ်သို့ ဆွဲတင်ပေးသည့် Filter
OUTDOOR_DIALOGUE_FILTER = (
    "highpass=f=160:poles=2,"
    "lowpass=f=8000:poles=2,"
    "equalizer=f=220:t=q:w=1.2:g=-4.0,"
    "equalizer=f=1800:t=q:w=1.0:g=4.5,"
    "equalizer=f=3200:t=q:w=1.2:g=5.0,"
    "afftdn=nr=12:nf=-35:tn=1"
)

# နောက်ဆုံးအဆင့်တွင် အသံလုံးဝမပျက်တောက်စေဘဲ စကားပြောသံ ကြည်လင်ကျယ်လောင်စေမည့် Smooth Mastering Filter
FINAL_SMOOTH_MASTER = (
    "equalizer=f=2500:t=q:w=1.2:g=3.0,"
    "acompressor=threshold=-16dB:ratio=2.0:attack=25:release=250:makeup=4dB,"
    "alimiter=limit=0.96:attack=5:release=50"
)

# --- Render Web Service အတွက် Health Check Server ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Continuous Studio AI Denoise Bot is running!", 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


class CancelledError(Exception):
    pass


def ensure_ai_engine():
    """DeepFilterNet3 AI Binary ရှိမရှိ စစ်ဆေးပြီး မရှိပါက အလိုအလျောက် ဒေါင်းလုဒ်ဆွဲပေးသည့် Function"""
    if os.path.exists(DEEP_FILTER_BIN) and os.access(DEEP_FILTER_BIN, os.X_OK):
        return DEEP_FILTER_BIN

    if os.path.exists(FALLBACK_BIN) and os.access(FALLBACK_BIN, os.X_OK):
        return FALLBACK_BIN

    print("⬇️ DeepFilterNet3 AI Engine ကို ဒေါင်းလုဒ်ဆွဲနေပါသည်...")
    urllib.request.urlretrieve(DEEP_FILTER_URL, FALLBACK_BIN)
    os.chmod(FALLBACK_BIN, 0o755)
    print("✅ DeepFilterNet3 AI Engine အဆင်သင့်ဖြစ်ပါပြီ။")
    return FALLBACK_BIN


def get_main_keyboard():
    """Start 🔄 နှင့် Cancel ❌ ကီးဘုတ် တည်ဆောက်ခြင်း"""
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("Start 🔄"), KeyboardButton("Cancel ❌"))
    return markup


def make_progress_bar(percent):
    """ရာခိုင်နှုန်းအလိုက် Progress Bar ပုံစံ ထုတ်ပေးခြင်း"""
    filled = int(percent // 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{bar}] {percent}%"


def safe_edit_message(chat_id, message_id, text):
    """Telegram Message Edit Error မတက်အောင် ကာကွယ်ပေးခြင်း"""
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


def check_cancelled(chat_id):
    """User က Cancel နှိပ်ထားခြင်း ရှိ/မရှိ စစ်ဆေးခြင်း"""
    if cancel_current_job or (chat_id in cancelled_users):
        raise CancelledError("လုပ်ဆောင်မှုကို ရပ်တန့်လိုက်ပါပြီ။")


def continuous_ai_enhance_with_progress(input_wav, output_wav, chat_id, msg_id, uid):
    """
    အသံကို အပိုင်း (Chunk) လုံးဝမခွဲတော့ဘဲ တစ်ဆက်တည်း (Single Continuous Pass) သန့်စင်သဖြင့်
    အသံပျက်တောက်မှု (Choppy) 0% ဖြစ်ပြီး နောက်ခံသီချင်းနှင့် စကားသံ လုံးဝချောမွေ့နေမည့် Function
    """
    ai_bin = ensure_ai_engine()
    base_audio, sample_rate = sf.read(input_wav, dtype='float32')

    if base_audio.ndim > 1:
        base_audio = np.mean(base_audio, axis=1)

    total_samples = len(base_audio)
    if total_samples == 0:
        raise ValueError("အသံဖိုင်ထဲတွင် အသံဒေတာ လုံးဝမပါဝင်ပါ။")

    duration_sec = total_samples / float(sample_rate)
    # Render CPU ပေါ်တွင် DeepFilterNet3 ၏ အမှန်တကယ် ကြာချိန်ကို တွက်ချက်ခြင်း
    estimated_process_sec = max(3.0, duration_sec * 0.38)

    ai_out_dir = os.path.join(TEMP_DIR, f"{uid}_ai_full")
    os.makedirs(ai_out_dir, exist_ok=True)
    expected_ai_wav = os.path.join(ai_out_dir, os.path.basename(input_wav))

    try:
        # "-a 12" ဖြင့် AI ကို ညင်သာစွာ မောင်းနှင်ပြီး တစ်ဆက်တည်း ဖိုင်တစ်ခုလုံးကို သန့်စင်မည်
        ai_cmd = [
            ai_bin,
            "-D",
            "-a", "12",
            "-o", ai_out_dir,
            input_wav
        ]
        proc = subprocess.Popen(ai_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        start_time = time.time()
        last_edit_time = 0

        # AI Engine အလုပ်လုပ်နေစဉ် Cancel စစ်ဆေးခြင်းနှင့် Live % ပြသခြင်း
        while proc.poll() is None:
            if cancel_current_job or (chat_id in cancelled_users):
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
                raise CancelledError("လုပ်ဆောင်မှုကို ရပ်တန့်လိုက်ပါပြီ။")

            elapsed = time.time() - start_time
            percent = min(95, int((elapsed / estimated_process_sec) * 100))
            now = time.time()

            if now - last_edit_time >= 1.8:
                bar_str = make_progress_bar(percent)
                safe_edit_message(
                    chat_id,
                    msg_id,
                    f"🎧 အသံမပျက်တောက်စေဘဲ တစ်ဆက်တည်း သန့်စင်နေပါသည်...\n{bar_str}"
                )
                last_edit_time = now

            time.sleep(0.3)

        _, stderr_text = proc.communicate()
        if proc.returncode != 0 or not os.path.exists(expected_ai_wav):
            raise RuntimeError(f"AI Engine Error: {stderr_text.strip()}")

        safe_edit_message(
            chat_id,
            msg_id,
            f"🎧 အသံမပျက်တောက်စေဘဲ တစ်ဆက်တည်း သန့်စင်နေပါသည်...\n{make_progress_bar(100)}"
        )

        ai_audio, _ = sf.read(expected_ai_wav, dtype='float32')
        if ai_audio.ndim > 1:
            ai_audio = np.mean(ai_audio, axis=1)

        if len(ai_audio) > total_samples:
            ai_audio = ai_audio[:total_samples]
        elif len(ai_audio) < total_samples:
            ai_audio = np.pad(ai_audio, (0, total_samples - len(ai_audio)))

        # Studio Parallel Voice Boost Mixing:
        # 65% Pure AI Voice + 35% De-Winded EQ Track
        # ဤအချိုးသည် သီချင်းသံနှင့် Meme အသံများကို မပျက်စီးစေဘဲ လူစကားပြောသံကို ရှေ့သို့ ဆွဲထုတ်ပေးသည်
        final_audio = (0.65 * ai_audio) + (0.35 * base_audio)

        max_val = np.max(np.abs(final_audio))
        if max_val > 0:
            final_audio = (final_audio / max_val) * 0.95

        sf.write(output_wav, final_audio, sample_rate, subtype='PCM_16')

    finally:
        if os.path.exists(expected_ai_wav):
            try:
                os.remove(expected_ai_wav)
            except OSError:
                pass
        if os.path.exists(ai_out_dir):
            try:
                os.rmdir(ai_out_dir)
            except OSError:
                pass
        del base_audio
        gc.collect()


def process_single_job(job):
    """Queue ထဲမှ ဖိုင်တစ်ဖိုင်ချင်းစီကို အစအဆုံး သန့်စင်ပေးသည့် Function"""
    global current_job_chat_id, cancel_current_job

    message = job['message']
    status_msg = job['status_msg']
    chat_id = message.chat.id
    content_type = message.content_type
    media = getattr(message, content_type)

    if chat_id in cancelled_users:
        safe_edit_message(chat_id, status_msg.message_id, "🛑 **လုပ်ဆောင်မှုကို ပယ်ဖျက်လိုက်ပါပြီ။**")
        return

    with state_lock:
        current_job_chat_id = chat_id
        cancel_current_job = False

    uid = f"{chat_id}_{uuid.uuid4().hex[:8]}"
    input_file = os.path.join(TEMP_DIR, f"{uid}_input")
    raw_wav = os.path.join(TEMP_DIR, f"{uid}_raw.wav")
    clean_wav = os.path.join(TEMP_DIR, f"{uid}_clean.wav")
    output_file = ""
    current_step = "ဖိုင်ဒေါင်းလုဒ်ဆွဲခြင်း"

    try:
        safe_edit_message(chat_id, status_msg.message_id, "📥 ဖိုင်ကို ဒေါင်းလုဒ်ဆွဲနေပါသည်... (0%)")

        file_info = bot.get_file(media.file_id)
        check_cancelled(chat_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open(input_file, 'wb') as f:
            f.write(downloaded_file)
        del downloaded_file
        gc.collect()

        check_cancelled(chat_id)

        # အဆင့် ၁ - Outdoor လေတိုးသံကို ဖြတ်ချပြီး လူစကားပြောသံ Frequency ကို မြှင့်တင်ခြင်း
        current_step = "FFmpeg ဖြင့် လေသံစစ်ထုတ်ခြင်းနှင့် လူသံ EQ ချိန်ညှိခြင်း"
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            "🔄 အပြင်လေတိုးသံကို စစ်ထုတ်ပြီး လူစကားသံ Frequency ကို မြှင့်တင်နေပါသည်..."
        )

        extract_cmd = [
            "ffmpeg", "-y", "-i", input_file,
            "-vn",
            "-af", OUTDOOR_DIALOGUE_FILTER,
            "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "1",
            "-loglevel", "error", raw_wav
        ]
        res = subprocess.run(extract_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"အသံဆွဲထုတ်၍ မရပါ:\n{res.stderr.strip()}")

        check_cancelled(chat_id)

        # အဆင့် ၂ - တစ်ဆက်တည်း (Continuous) AI Voice Enhancement ပြုလုပ်ခြင်း
        current_step = "Continuous AI Voice Enhancement ပြုလုပ်ခြင်း"
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            f"🎧 အသံမပျက်တောက်စေဘဲ တစ်ဆက်တည်း သန့်စင်နေပါသည်...\n{make_progress_bar(0)}"
        )

        continuous_ai_enhance_with_progress(
            raw_wav, clean_wav,
            chat_id=chat_id,
            msg_id=status_msg.message_id,
            uid=uid
        )

        check_cancelled(chat_id)

        # အဆင့် ၃ - Smooth Mastering ဖြင့် မူရင်း Format အတိုင်း ပြန်ပို့ခြင်း
        current_step = "ဖိုင်ပြန်ပေါင်းခြင်းနှင့် ပေးပို့ခြင်း"
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            "📤 100% ပြီးစီးပါပြီ! ချောမွေ့ကြည်လင်သော ဖိုင်ကို ပြန်ပို့ပေးနေပါသည်..."
        )

        if content_type in ['video', 'video_note']:
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.mp4")
            merge_cmd = [
                "ffmpeg", "-y", "-i", input_file, "-i", clean_wav,
                "-c:v", "copy",
                "-af", FINAL_SMOOTH_MASTER,
                "-c:a", "aac", "-b:a", "192k",
                "-map", "0:v:0", "-map", "1:a:0", "-shortest",
                "-loglevel", "error", output_file
            ]
            res = subprocess.run(merge_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"ဗီဒီယိုပြန်ပေါင်းရာတွင် အမှားဖြစ်ပွားပါသည်:\n{res.stderr.strip()}")

            with open(output_file, 'rb') as f:
                bot.send_video(
                    chat_id, f,
                    caption="✅ တစ်ဆက်တည်း (Continuous Pass) စနစ်ဖြင့် အသံပျက်တောက်မှု လုံးဝမရှိဘဲ လူစကားသံကို ကြည်လင်စွာ မြှင့်တင်ပေးထားပါသည်။",
                    reply_to_message_id=message.message_id,
                    reply_markup=get_main_keyboard()
                )

        elif content_type == 'voice':
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.ogg")
            ogg_cmd = [
                "ffmpeg", "-y", "-i", clean_wav,
                "-af", FINAL_SMOOTH_MASTER,
                "-c:a", "libopus", "-b:a", "64k",
                "-loglevel", "error", output_file
            ]
            res = subprocess.run(ogg_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"Voice ပြောင်းရာတွင် အမှားဖြစ်ပွားပါသည်:\n{res.stderr.strip()}")

            with open(output_file, 'rb') as f:
                bot.send_voice(
                    chat_id, f,
                    caption="✅ တစ်ဆက်တည်း (Continuous Pass) စနစ်ဖြင့် အသံပျက်တောက်မှု လုံးဝမရှိဘဲ လူစကားသံကို ကြည်လင်စွာ မြှင့်တင်ပေးထားပါသည်။",
                    reply_to_message_id=message.message_id,
                    reply_markup=get_main_keyboard()
                )

        elif content_type == 'audio':
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.mp3")
            mp3_cmd = [
                "ffmpeg", "-y", "-i", clean_wav,
                "-af", FINAL_SMOOTH_MASTER,
                "-c:a", "libmp3lame", "-b:a", "192k",
                "-loglevel", "error", output_file
            ]
            res = subprocess.run(mp3_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"MP3 ပြောင်းရာတွင် အမှားဖြစ်ပွားပါသည်:\n{res.stderr.strip()}")

            with open(output_file, 'rb') as f:
                bot.send_audio(
                    chat_id, f,
                    caption="✅ တစ်ဆက်တည်း (Continuous Pass) စနစ်ဖြင့် အသံပျက်တောက်မှု လုံးဝမရှိဘဲ လူစကားသံကို ကြည်လင်စွာ မြှင့်တင်ပေးထားပါသည်။",
                    reply_to_message_id=message.message_id,
                    reply_markup=get_main_keyboard()
                )

        try:
            bot.delete_message(chat_id, status_msg.message_id)
        except Exception:
            pass

    except CancelledError:
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            "🛑 **လုပ်ဆောင်မှုကို အောင်မြင်စွာ ရပ်တန့်လိုက်ပါပြီ။**"
        )

    except Exception as e:
        error_detail = str(e)
        print(traceback.format_exc())
        error_msg = (
            f"❌ **Error ဖြစ်ပွားနေပါသည်!**\n\n"
            f"📍 **အဆင့်:** {current_step}\n"
            f"⚠️ **အကြောင်းရင်း:** `{error_detail[:300]}`"
        )
        try:
            bot.edit_message_text(error_msg, chat_id=chat_id, message_id=status_msg.message_id, parse_mode="Markdown")
        except Exception:
            bot.reply_to(message, f"❌ Error ({current_step}): {error_detail[:300]}", reply_markup=get_main_keyboard())

    finally:
        for path in [input_file, raw_wav, clean_wav, output_file]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        with state_lock:
            current_job_chat_id = None
            cancel_current_job = False
        gc.collect()
        time.sleep(1)


def queue_worker():
    """ဖိုင်များကို တစ်ခုပြီးမှတစ်ခု အစဉ်လိုက် လုပ်ဆောင်ပေးမည့် Worker"""
    while True:
        job = job_queue.get()
        try:
            process_single_job(job)
        except Exception as e:
            print(f"Worker Error: {e}")
        finally:
            job_queue.task_done()


# --- Start 🔄 နှင့် /start Command Handler ---
@bot.message_handler(commands=['start', 'help'])
@bot.message_handler(func=lambda msg: msg.text == "Start 🔄")
def send_welcome(message):
    chat_id = message.chat.id
    with state_lock:
        cancelled_users.discard(chat_id)

    welcome_text = (
        "👋 မင်္ဂလာပါ! **Continuous Smooth AI Voice Cleaner Bot** မှ ကြိုဆိုပါတယ်။\n\n"
        "📹 **Video ဖိုင်**\n"
        "🎵 **Audio (MP3/WAV) ဖိုင်**\n"
        "🎤 **Voice Message**\n\n"
        "ကြိုက်နှစ်သက်ရာ ပေးပို့နိုင်ပါတယ်။ အသံကို အပိုင်းမခွဲဘဲ **တစ်ဆက်တည်း (Single Continuous Pass)** သန့်စင်ပေးသဖြင့် အသံပျက်တောက်မှု လုံးဝမရှိဘဲ လူစကားပြောသံကို ကြည်လင်စွာ မြှင့်တင်ပေးပါမည်။"
    )
    bot.send_message(
        chat_id,
        welcome_text,
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )


# --- Cancel ❌ နှင့် /cancel Command Handler ---
@bot.message_handler(commands=['cancel'])
@bot.message_handler(func=lambda msg: msg.text == "Cancel ❌")
def handle_cancel(message):
    global cancel_current_job
    chat_id = message.chat.id

    with state_lock:
        cancelled_users.add(chat_id)
        is_current = (current_job_chat_id == chat_id)
        if is_current:
            cancel_current_job = True

    if is_current or not job_queue.empty():
        bot.reply_to(
            message,
            "🛑 သင်ပေးပို့ထားသော လုပ်ဆောင်မှုများကို ရပ်တန့်နေပါသည်...",
            reply_markup=get_main_keyboard()
        )
    else:
        with state_lock:
            cancelled_users.discard(chat_id)
        bot.reply_to(
            message,
            "ℹ️ လက်ရှိတွင် လုပ်ဆောင်နေသော ဖိုင်မရှိပါ။ ဖိုင်အသစ် ပေးပို့နိုင်ပါသည်။",
            reply_markup=get_main_keyboard()
        )


# --- Video / Audio / Voice Handler ---
@bot.message_handler(content_types=['video', 'audio', 'voice', 'video_note'])
def handle_media(message):
    chat_id = message.chat.id
    content_type = message.content_type
    media = getattr(message, content_type)

    if media.file_size and media.file_size > 20 * 1024 * 1024:
        bot.reply_to(
            message,
            "⚠️ ဖိုင်အရွယ်အစား 20MB ထက် ကျော်လွန်နေသဖြင့် ဒေါင်းလုဒ်ဆွဲ၍ မရနိုင်ပါ။",
            reply_markup=get_main_keyboard()
        )
        return

    with state_lock:
        cancelled_users.discard(chat_id)
        is_busy = (current_job_chat_id is not None) or (not job_queue.empty())
        waiting_count = job_queue.qsize() + (1 if current_job_chat_id is not None else 0)

    if is_busy:
        status_msg = bot.reply_to(
            message,
            f"⏳ ရှေ့ကဖိုင်ကို အရင်လုပ်ဆောင်နေပါသည်။\n"
            f"📋 သင့်ဖိုင်ကို အလှည့်ကျစာရင်း **(Queue #{waiting_count})** တွင် ထည့်သွင်းထားပါသည်...",
            parse_mode="Markdown"
        )
    else:
        status_msg = bot.reply_to(message, "📥 ဖိုင်ကို လက်ခံရရှိပါပြီ။ စတင်သန့်စင်နေပါသည်...")

    job_queue.put({
        'message': message,
        'status_msg': status_msg
    })


if __name__ == "__main__":
    Thread(target=run_web_server, daemon=True).start()
    ensure_ai_engine()
    Thread(target=queue_worker, daemon=True).start()
    print("🤖 Continuous Smooth AI Voice Bot is polling...")
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
    
