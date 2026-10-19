import os
import gc
import time
import uuid
import queue
import traceback
import subprocess
from threading import Thread, Lock
from flask import Flask
import numpy as np
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
import soundfile as sf
import noisereduce as nr

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

# လူစကားမပြောချိန်တွင် အပြင်သံကို ပိတ်ထားပြီး (agate)၊ လူစကားပြောသံကိုသာ သီးသန့်ချဲ့ပေးမည့် Filter
VOICE_ISOLATION_FILTER = (
    "highpass=f=120:poles=2,"
    "lowpass=f=6000:poles=2,"
    "agate=threshold=0.02:ratio=4:attack=10:release=180,"
    "equalizer=f=280:t=q:w=1.0:g=3,"
    "equalizer=f=2800:t=q:w=1.2:g=6,"
    "acompressor=threshold=-18dB:ratio=3:attack=5:release=100:makeup=7dB,"
    "alimiter=limit=0.95"
)

# --- Render Web Service အတွက် Health Check Server ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Denoise Bot is running!", 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


class CancelledError(Exception):
    pass


def get_main_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("Start 🔄"), KeyboardButton("Cancel ❌"))
    return markup


def make_progress_bar(percent):
    filled = int(percent // 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{bar}] {percent}%"


def safe_edit_message(chat_id, message_id, text):
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


def check_cancelled(chat_id):
    if cancel_current_job or (chat_id in cancelled_users):
        raise CancelledError("လုပ်ဆောင်မှုကို ရပ်တန့်လိုက်ပါပြီ။")


def reduce_audio_noise_with_progress(input_wav, output_wav, chat_id, msg_id):
    """Dual-Pass Noise Reduction နှင့် Smart Voice Gate ဖြင့် အပြင်သံကို အပြတ်ရှင်းပေးသည့် Function"""
    audio_data, sample_rate = sf.read(input_wav, dtype='float32')

    if audio_data.ndim > 1:
        audio_data = np.mean(audio_data, axis=1)

    total_samples = len(audio_data)
    if total_samples == 0:
        raise ValueError("အသံဖိုင်ထဲတွင် အသံဒေတာ လုံးဝမပါဝင်ပါ။")

    # Render RAM မပြည့်စေရန် ၆ စက္ကန့်စာ အပိုင်းလိုက် ခွဲထုတ်မည်
    chunk_size = max(sample_rate * 2, min(sample_rate * 6, total_samples // 4))
    pad = int(sample_rate * 0.25)

    cleaned_chunks = []
    last_edit_time = 0

    for start in range(0, total_samples, chunk_size):
        check_cancelled(chat_id)

        end = min(start + chunk_size, total_samples)
        pad_start = max(0, start - pad)
        pad_end = min(total_samples, end + pad)

        chunk = audio_data[pad_start:pad_end]

        # Pass 1: ပြောင်းလဲနေသော အပြင်ဆူညံသံများ (ကားသံ၊ လူသံ) ကို 95% ဖယ်ရှားခြင်း
        pass1 = nr.reduce_noise(
            y=chunk,
            sr=sample_rate,
            stationary=False,
            prop_decrease=0.95,
            time_constant_s=1.0,
            freq_mask_smooth_hz=250,
            time_mask_smooth_ms=50,
            n_fft=1024,
            n_jobs=1
        )

        check_cancelled(chat_id)

        # Pass 2: ကျန်ရှိနေသော တသမတ်တည်း ဆူညံသံ (လေသံ၊ ပန်ကာသံ) ကို ထပ်မံဖယ်ရှားခြင်း
        pass2 = nr.reduce_noise(
            y=pass1,
            sr=sample_rate,
            stationary=True,
            prop_decrease=0.90,
            n_fft=1024,
            n_jobs=1
        )

        # Smart Voice Gate: လူစကားမပြောသည့် အပိုင်းများရှိ လက်ကျန် Noise ကို နှိမ်ချခြင်း
        abs_signal = np.abs(pass2)
        noise_floor = np.percentile(abs_signal, 60) * 1.5
        gate_mask = np.where(abs_signal < noise_floor, 0.15, 1.0).astype(np.float32)
        gated_chunk = pass2 * gate_mask

        trim_left = start - pad_start
        trim_right = len(gated_chunk) - (pad_end - end)
        cleaned_chunks.append(gated_chunk[trim_left:trim_right])

        percent = int((end / total_samples) * 100)
        now = time.time()

        if now - last_edit_time >= 2.0 or percent == 100:
            bar_str = make_progress_bar(percent)
            safe_edit_message(
                chat_id,
                msg_id,
                f"🎧 အပြင်ဆူညံသံများကို အထူးသန့်စင်နေပါသည်...\n{bar_str}"
            )
            last_edit_time = now

    final_audio = np.concatenate(cleaned_chunks)

    # လူစကားသံကို အမြင့်ဆုံးအဆင့်သို့ ဆွဲတင်ခြင်း (Peak Normalization)
    max_val = np.max(np.abs(final_audio))
    if max_val > 0:
        final_audio = (final_audio / max_val) * 0.95

    sf.write(output_wav, final_audio, sample_rate)

    del audio_data, cleaned_chunks, final_audio
    gc.collect()


def process_single_job(job):
    """Queue ထဲမှ ဖိုင်တစ်ဖိုင်ချင်းစီကို အစအဆုံး လုပ်ဆောင်ပေးသည့် Function"""
    global current_job_chat_id, cancel_current_job

    message = job['message']
    status_msg = job['status_msg']
    chat_id = message.chat.id
    content_type = message.content_type
    media = getattr(message, content_type)

    # Queue စောင့်နေစဉ် Cancel နှိပ်ထားပါက ကျော်သွားမည်
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

        # အဆင့် ၂ - FFmpeg ဖြင့် အသံထုတ်ယူပြီး ပထမအဆင့် FFT Denoise ပြုလုပ်ခြင်း
        current_step = "FFmpeg ဖြင့် အသံလမ်းကြောင်း ဆွဲထုတ်ခြင်း"
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            "🔄 အသံလမ်းကြောင်းကို ဆွဲထုတ်ပြီး ပထမအဆင့် သန့်စင်နေပါသည်..."
        )

        # 16000 Hz သည် လူ့စကားသံအတွက် အကြည်လင်ဆုံးနှင့် Render RAM အသက်သာဆုံး ဖြစ်သည်
        extract_cmd = [
            "ffmpeg", "-y", "-i", input_file,
            "-vn",
            "-af", "highpass=f=120,lowpass=f=6000,afftdn=nr=20:nf=-35:tn=1",
            "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            "-loglevel", "error", raw_wav
        ]
        res = subprocess.run(extract_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"အသံဆွဲထုတ်၍ မရပါ:\n{res.stderr.strip()}")

        check_cancelled(chat_id)

        # အဆင့် ၃ - Dual-Pass Python Denoise နှင့် Smart Gate
        current_step = "Background Noise အထူးဖယ်ရှားခြင်း"
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            f"🎧 အပြင်ဆူညံသံများကို အထူးသန့်စင်နေပါသည်...\n{make_progress_bar(0)}"
        )

        reduce_audio_noise_with_progress(
            raw_wav, clean_wav,
            chat_id=chat_id,
            msg_id=status_msg.message_id
        )

        check_cancelled(chat_id)

        # အဆင့် ၄ - Vocal Compressor + EQ ထည့်ပြီး ပြန်ပို့ခြင်း
        current_step = "လူစကားသံ ချဲ့ခြင်းနှင့် ဖိုင်ပြန်ပို့ခြင်း"
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            "📤 100% ပြီးစီးပါပြီ! လူသံမြှင့်တင်ပြီးသော ဖိုင်ကို ပြန်ပို့ပေးနေပါသည်..."
        )

        if content_type in ['video', 'video_note']:
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.mp4")
            merge_cmd = [
                "ffmpeg", "-y", "-i", input_file, "-i", clean_wav,
                "-c:v", "copy",
                "-af", VOICE_ISOLATION_FILTER,
                "-c:a", "aac", "-b:a", "160k",
                "-map", "0:v:0", "-map", "1:a:0", "-shortest",
                "-loglevel", "error", output_file
            ]
            res = subprocess.run(merge_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"ဗီဒီယိုပြန်ပေါင်းရာတွင် အမှားဖြစ်ပွားပါသည်:\n{res.stderr.strip()}")

            with open(output_file, 'rb') as f:
                bot.send_video(
                    chat_id, f,
                    caption="✅ အပြင်ဆူညံသံများကို အထူးဖယ်ရှားပြီး လူစကားသံကို သီးသန့်မြှင့်တင်ပေးထားပါသည်။",
                    reply_to_message_id=message.message_id,
                    reply_markup=get_main_keyboard()
                )

        elif content_type == 'voice':
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.ogg")
            ogg_cmd = [
                "ffmpeg", "-y", "-i", clean_wav,
                "-af", VOICE_ISOLATION_FILTER,
                "-c:a", "libopus", "-b:a", "64k",
                "-loglevel", "error", output_file
            ]
            res = subprocess.run(ogg_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"Voice ပြောင်းရာတွင် အမှားဖြစ်ပွားပါသည်:\n{res.stderr.strip()}")

            with open(output_file, 'rb') as f:
                bot.send_voice(
                    chat_id, f,
                    caption="✅ အပြင်ဆူညံသံများကို အထူးဖယ်ရှားပြီး လူစကားသံကို သီးသန့်မြှင့်တင်ပေးထားပါသည်။",
                    reply_to_message_id=message.message_id,
                    reply_markup=get_main_keyboard()
                )

        elif content_type == 'audio':
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.mp3")
            mp3_cmd = [
                "ffmpeg", "-y", "-i", clean_wav,
                "-af", VOICE_ISOLATION_FILTER,
                "-c:a", "libmp3lame", "-b:a", "160k",
                "-loglevel", "error", output_file
            ]
            res = subprocess.run(mp3_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"MP3 ပြောင်းရာတွင် အမှားဖြစ်ပွားပါသည်:\n{res.stderr.strip()}")

            with open(output_file, 'rb') as f:
                bot.send_audio(
                    chat_id, f,
                    caption="✅ အပြင်ဆူညံသံများကို အထူးဖယ်ရှားပြီး လူစကားသံကို သီးသန့်မြှင့်တင်ပေးထားပါသည်။",
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
        # ဖိုင်တစ်ခု ပြီးဆုံးတိုင်း Temp ဖိုင်များနှင့် RAM ကို အပြည့်အဝ ရှင်းလင်းမည်
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
        # နောက်ဖိုင်တစ်ခု မစတင်မီ Render CPU/RAM အနားရစေရန် ၁ စက္ကန့် စောင့်မည်
        time.sleep(1)


def queue_worker():
    """Queue ထဲရှိ ဖိုင်များကို တစ်ခုပြီးမှတစ်ခု အစဉ်လိုက် လုပ်ဆောင်ပေးမည့် Background Worker"""
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
        "👋 မင်္ဂလာပါ! **Ultra Voice Isolation & Noise Remover Bot** မှ ကြိုဆိုပါတယ်။\n\n"
        "📹 **Video ဖိုင်**\n"
        "🎵 **Audio (MP3/WAV) ဖိုင်**\n"
        "🎤 **Voice Message**\n\n"
        "ကြိုက်နှစ်သက်ရာ ပေးပို့နိုင်ပါတယ်။ အပြင်ဆူညံသံများကို **Dual-Pass စနစ်ဖြင့် အပြတ်ရှင်းပြီး** လူစကားပြောသံကို သီးသန့် ဆွဲထုတ်ပေးပါမယ်။\n\n"
        "🛡️ *ဖိုင်အများကြီး တစ်ပြိုင်နက်ပို့လျှင်လည်း Server Error မတက်စေရန် တစ်ခုပြီးမှတစ်ခု အလှည့်ကျ လုပ်ဆောင်ပေးပါသည်။*"
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


# --- Video / Audio / Voice Handler (Queue ထဲသို့ ထည့်သွင်းခြင်း) ---
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
            f"📋 သင့်ဖိုင်ကို အလှည့်ကျစာရင်း **(Queue #{waiting_count})** တွင် ထည့်သွင်းထားပါသည်... ရှေ့ဖိုင်ပြီးသည်နှင့် အလိုအလျောက် စတင်ပါမည်။",
            parse_mode="Markdown"
        )
    else:
        status_msg = bot.reply_to(message, "📥 ဖိုင်ကို လက်ခံရရှိပါပြီ။ စတင်လုပ်ဆောင်နေပါသည်...")

    # Queue ထဲသို့ အစဉ်လိုက် ထည့်သွင်းခြင်း
    job_queue.put({
        'message': message,
        'status_msg': status_msg
    })


if __name__ == "__main__":
    # Flask Web Server နှင့် Queue Worker Thread တို့ကို စတင်မည်
    Thread(target=run_web_server, daemon=True).start()
    Thread(target=queue_worker, daemon=True).start()
    print("🤖 Sequential Queue Denoise Bot is polling...")
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
    
