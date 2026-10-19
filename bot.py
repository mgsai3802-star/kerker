import os
import gc
import time
import uuid
import queue
import shutil
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

# အဆင့် ၁ တွင် မိုက်ခ်လေတိုးသံ (Wind Rumble & Mud) ကိုသာ ကွက်၍ဖယ်ရှားပြီး သီချင်းနှင့် လူသံကို မပျက်စီးစေသည့် Filter
DEWIND_EXTRACT_FILTER = (
    "highpass=f=150:poles=2,"
    "equalizer=f=240:t=q:w=1.2:g=-4.5,"
    "equalizer=f=2200:t=q:w=1.2:g=3.0,"
    "equalizer=f=3800:t=q:w=1.4:g=2.5,"
    "afftdn=nr=10:nf=-38:tn=1"
)

# နောက်ဆုံးအဆင့်တွင် အသံပျက်တောက်စေသော Compressor မသုံးဘဲ လူသံကြည်လင်စေရန် ညှိပေးသည့် Smooth Filter
SMOOTH_MASTER_FILTER = (
    "equalizer=f=2500:t=q:w=1.3:g=2.5,"
    "alimiter=limit=0.96:attack=5:release=50"
)

# --- Render Web Service အတွက် Health Check Server ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Smooth Parallel AI Denoise Bot is running!", 200

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


def ai_parallel_smooth_enhance(input_wav, output_wav, chat_id, msg_id, uid):
    """
    နောက်ခံသီချင်းသံ မပျောက်စေဘဲ အသံပျက်တောက်မှု (Choppy) လုံးဝမဖြစ်စေရန်
    Gentle AI (-a 14 dB, No Post-Filter) နှင့် Continuous De-Winded Track ကို
    70% / 30% Parallel Mixing ပြုလုပ်ပေးသည့် Function
    """
    ai_bin = ensure_ai_engine()
    base_audio, sample_rate = sf.read(input_wav, dtype='float32')

    if base_audio.ndim > 1:
        base_audio = np.mean(base_audio, axis=1)

    total_samples = len(base_audio)
    if total_samples == 0:
        raise ValueError("အသံဖိုင်ထဲတွင် အသံဒေတာ လုံးဝမပါဝင်ပါ။")

    # AI RNN State မပြတ်တောက်စေရန် အပိုင်း (Chunk) ကို ၈ စက္ကန့်မှ ၁၂ စက္ကန့်အထိ ခပ်ကြီးကြီး ယူမည်
    target_chunk_sec = max(8.0, min(12.0, (total_samples / sample_rate) / 4.0))
    step_samples = int(sample_rate * target_chunk_sec)
    # အပိုင်းတစ်ခုနှင့်တစ်ခုကြား လုံးဝအဆက်အစပ် မသိသာစေရန် 1.0 စက္ကန့် Overlap ထားမည်
    overlap_samples = int(sample_rate * 1.0)

    chunk_work_dir = os.path.join(TEMP_DIR, f"{uid}_chunks")
    chunk_out_dir = os.path.join(TEMP_DIR, f"{uid}_ai_out")
    os.makedirs(chunk_work_dir, exist_ok=True)
    os.makedirs(chunk_out_dir, exist_ok=True)

    ai_audio = np.zeros(total_samples, dtype=np.float32)
    weight_sum = np.zeros(total_samples, dtype=np.float32)

    starts = list(range(0, total_samples, step_samples))
    total_chunks = len(starts)
    last_edit_time = 0

    try:
        for idx, start in enumerate(starts):
            check_cancelled(chat_id)

            end = min(start + step_samples + overlap_samples, total_samples)
            raw_chunk = base_audio[start:end]
            chunk_len = len(raw_chunk)

            if chunk_len == 0:
                continue

            chunk_filename = f"part_{idx}.wav"
            chunk_in_path = os.path.join(chunk_work_dir, chunk_filename)
            chunk_out_path = os.path.join(chunk_out_dir, chunk_filename)

            sf.write(chunk_in_path, raw_chunk, sample_rate, subtype='PCM_16')

            # အရေးကြီးချက် - အသံပျက်တောက်စေသော "--pf" ကို လုံးဝမသုံးတော့ပါ။
            # "-a 14" (Max 14dB reduction) သုံးထားသဖြင့် နောက်ခံသီချင်းသံနှင့် စကားသံ လုံးဝမပြတ်တောက်တော့ပါ။
            ai_cmd = [
                ai_bin,
                "-D",
                "-a", "14",
                "-o", chunk_out_dir,
                chunk_in_path
            ]
            res = subprocess.run(ai_cmd, capture_output=True, text=True)
            if res.returncode != 0 or not os.path.exists(chunk_out_path):
                fallback_cmd = [ai_bin, "-D", "-o", chunk_out_dir, chunk_in_path]
                res2 = subprocess.run(fallback_cmd, capture_output=True, text=True)
                if res2.returncode != 0 or not os.path.exists(chunk_out_path):
                    raise RuntimeError(f"AI Engine Error: {res.stderr.strip() or res2.stderr.strip()}")

            cleaned_chunk, _ = sf.read(chunk_out_path, dtype='float32')
            if cleaned_chunk.ndim > 1:
                cleaned_chunk = np.mean(cleaned_chunk, axis=1)

            if len(cleaned_chunk) > chunk_len:
                cleaned_chunk = cleaned_chunk[:chunk_len]
            elif len(cleaned_chunk) < chunk_len:
                cleaned_chunk = np.pad(cleaned_chunk, (0, chunk_len - len(cleaned_chunk)))

            # Cosine (Hann-style) Smooth Crossfade Window တည်ဆောက်ခြင်း
            window = np.ones(chunk_len, dtype=np.float32)
            fade_len = min(overlap_samples, chunk_len // 2)
            if start > 0 and fade_len > 0:
                t = np.linspace(0.0, np.pi, fade_len, dtype=np.float32)
                window[:fade_len] = 0.5 * (1.0 - np.cos(t))
            if end < total_samples and fade_len > 0:
                t = np.linspace(0.0, np.pi, fade_len, dtype=np.float32)
                window[-fade_len:] = 0.5 * (1.0 + np.cos(t))

            ai_audio[start:end] += cleaned_chunk * window
            weight_sum[start:end] += window

            for p in [chunk_in_path, chunk_out_path]:
                if os.path.exists(p):
                    os.remove(p)

            percent = int(((idx + 1) / total_chunks) * 100)
            now = time.time()

            if now - last_edit_time >= 1.5 or percent == 100:
                bar_str = make_progress_bar(percent)
                safe_edit_message(
                    chat_id,
                    msg_id,
                    f"🎧 သီချင်းသံမပျက်စေဘဲ လေသံစစ်ထုတ်၍ လူသံမြှင့်တင်နေပါသည်...\n{bar_str}"
                )
                last_edit_time = now

        nonzero = weight_sum > 1e-6
        ai_audio[nonzero] /= weight_sum[nonzero]

        # Studio Parallel Mixing:
        # 70% AI Cleaned Voice + 30% De-Winded Continuous Base Audio
        # ဤနည်းဖြင့် နောက်ခံသီချင်းသံ လုံးဝမပျောက်သလို စကားသံလည်း လုံးဝမပျက်တောက်တော့ဘဲ ပိုမိုကြည်လင်လာမည်
        blended_audio = (0.70 * ai_audio) + (0.30 * base_audio)

        # Peak Normalization (အသံအကျယ်ဆုံးနှင့် အကြည်လင်ဆုံးဖြစ်စေရန်)
        max_val = np.max(np.abs(blended_audio))
        if max_val > 0:
            blended_audio = (blended_audio / max_val) * 0.95

        sf.write(output_wav, blended_audio, sample_rate, subtype='PCM_16')

    finally:
        shutil.rmtree(chunk_work_dir, ignore_errors=True)
        shutil.rmtree(chunk_out_dir, ignore_errors=True)
        del base_audio, ai_audio, weight_sum
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

        # အဆင့် ၁ - လေတိုးသံ (Wind Rumble) ကို EQ ဖြင့် အရင်စစ်ထုတ်ပြီး 48kHz WAV ဆွဲထုတ်ခြင်း
        current_step = "FFmpeg ဖြင့် လေတိုးသံ Frequency စစ်ထုတ်ခြင်း"
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            "🔄 မိုက်ခ်လေတိုးသံ Frequency များကို စစ်ထုတ်နေပါသည်..."
        )

        extract_cmd = [
            "ffmpeg", "-y", "-i", input_file,
            "-vn",
            "-af", DEWIND_EXTRACT_FILTER,
            "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "1",
            "-loglevel", "error", raw_wav
        ]
        res = subprocess.run(extract_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"အသံဆွဲထုတ်၍ မရပါ:\n{res.stderr.strip()}")

        check_cancelled(chat_id)

        # အဆင့် ၂ - Parallel Smooth AI Blending (Live % Progress)
        current_step = "Parallel AI Voice Boosting ပြုလုပ်ခြင်း"
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            f"🎧 သီချင်းသံမပျက်စေဘဲ လေသံစစ်ထုတ်၍ လူသံမြှင့်တင်နေပါသည်...\n{make_progress_bar(0)}"
        )

        ai_parallel_smooth_enhance(
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
                "-af", SMOOTH_MASTER_FILTER,
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
                    caption="✅ နောက်ခံသီချင်းနှင့် စကားသံ မပျက်တောက်စေဘဲ လေသံလျှော့ချ၍ လူသံကို ကြည်လင်စွာ မြှင့်တင်ပေးထားပါသည်။",
                    reply_to_message_id=message.message_id,
                    reply_markup=get_main_keyboard()
                )

        elif content_type == 'voice':
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.ogg")
            ogg_cmd = [
                "ffmpeg", "-y", "-i", clean_wav,
                "-af", SMOOTH_MASTER_FILTER,
                "-c:a", "libopus", "-b:a", "64k",
                "-loglevel", "error", output_file
            ]
            res = subprocess.run(ogg_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"Voice ပြောင်းရာတွင် အမှားဖြစ်ပွားပါသည်:\n{res.stderr.strip()}")

            with open(output_file, 'rb') as f:
                bot.send_voice(
                    chat_id, f,
                    caption="✅ နောက်ခံသီချင်းနှင့် စကားသံ မပျက်တောက်စေဘဲ လေသံလျှော့ချ၍ လူသံကို ကြည်လင်စွာ မြှင့်တင်ပေးထားပါသည်။",
                    reply_to_message_id=message.message_id,
                    reply_markup=get_main_keyboard()
                )

        elif content_type == 'audio':
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.mp3")
            mp3_cmd = [
                "ffmpeg", "-y", "-i", clean_wav,
                "-af", SMOOTH_MASTER_FILTER,
                "-c:a", "libmp3lame", "-b:a", "192k",
                "-loglevel", "error", output_file
            ]
            res = subprocess.run(mp3_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"MP3 ပြောင်းရာတွင် အမှားဖြစ်ပွားပါသည်:\n{res.stderr.strip()}")

            with open(output_file, 'rb') as f:
                bot.send_audio(
                    chat_id, f,
                    caption="✅ နောက်ခံသီချင်းနှင့် စကားသံ မပျက်တောက်စေဘဲ လေသံလျှော့ချ၍ လူသံကို ကြည်လင်စွာ မြှင့်တင်ပေးထားပါသည်။",
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
        "👋 မင်္ဂလာပါ! **Smooth Parallel AI Voice & Wind Cleaner Bot** မှ ကြိုဆိုပါတယ်။\n\n"
        "📹 **Video ဖိုင်**\n"
        "🎵 **Audio (MP3/WAV) ဖိုင်**\n"
        "🎤 **Voice Message**\n\n"
        "ကြိုက်နှစ်သက်ရာ ပေးပို့နိုင်ပါတယ်။ **Parallel Mixing စနစ်** ဖြင့် နောက်ခံသီချင်းသံနှင့် စကားသံများ လုံးဝမပျက်တောက်စေဘဲ လေတိုးသံကို လျှော့ချကာ လူစကားပြောသံကို ကြည်လင်စွာ မြှင့်တင်ပေးပါမည်။"
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
    print("🤖 Smooth Parallel AI Voice Bot is polling...")
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
    
