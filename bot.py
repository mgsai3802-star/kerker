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

job_queue = queue.Queue()
state_lock = Lock()
current_job_chat_id = None
cancel_current_job = False
cancelled_users = set()

# လေသံ (Wind) နှင့် ဆူညံသံပြင်းထန်မှုများကို အပြတ်ရှင်းပေးမည့် Professional FFmpeg Filter
PRO_WIND_FILTER = (
    "highpass=f=220:poles=2,"
    "lowpass=f=6000:poles=2,"
    "afftdn=nr=30:nf=-40:tn=1,"
    "acompressor=threshold=-20dB:ratio=3:attack=10:release=100:makeup=4dB,"
    "alimiter=limit=0.95"
)

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


def reduce_audio_noise_advanced(input_wav, output_wav, chat_id, msg_id):
    """လေသံဆူညံမှုများကို အပိုင်းလိုက်ခွဲ၍ % တက်စနစ်ဖြင့် ချောမွေ့စွာ ဖယ်ရှားခြင်း"""
    audio_data, sample_rate = sf.read(input_wav, dtype='float32')

    if audio_data.ndim > 1:
        audio_data = np.mean(audio_data, axis=1)

    total_samples = len(audio_data)
    if total_samples == 0:
        raise ValueError("အသံဖိုင်ထဲတွင် အသံဒေတာ လုံးဝမပါဝင်ပါ။")

    chunk_size = max(sample_rate * 2, min(sample_rate * 6, total_samples // 4))
    pad = int(sample_rate * 0.3)

    cleaned_chunks = []
    last_edit_time = 0

    for start in range(0, total_samples, chunk_size):
        check_cancelled(chat_id)

        end = min(start + chunk_size, total_samples)
        pad_start = max(0, start - pad)
        pad_end = min(total_samples, end + pad)

        chunk = audio_data[pad_start:pad_end]

        cleaned_chunk = nr.reduce_noise(
            y=chunk,
            sr=sample_rate,
            stationary=False,
            prop_decrease=0.88,
            time_constant_s=1.0,
            freq_mask_smooth_hz=300,
            time_mask_smooth_ms=50,
            n_fft=1024,
            n_jobs=1
        )

        trim_left = start - pad_start
        trim_right = len(cleaned_chunk) - (pad_end - end)
        cleaned_chunks.append(cleaned_chunk[trim_left:trim_right])

        percent = int((end / total_samples) * 100)
        now = time.time()

        if now - last_edit_time >= 1.5 or percent == 100:
            bar_str = make_progress_bar(percent)
            safe_edit_message(
                chat_id,
                msg_id,
                f"🌪️ လေသံနှင့် အပြင်ဆူညံသံများကို ဖယ်ရှားနေပါသည်...\n{bar_str}"
            )
            last_edit_time = now

    final_audio = np.concatenate(cleaned_chunks)

    max_val = np.max(np.abs(final_audio))
    if max_val > 0:
        final_audio = (final_audio / max_val) * 0.95

    sf.write(output_wav, final_audio, sample_rate)

    del audio_data, cleaned_chunks, final_audio
    gc.collect()


def process_single_job(job):
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

        current_step = "FFmpeg ဖြင့် လေသံကြိမ်နှုန်းများ ဖြတ်ထုတ်ခြင်း"
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            "🔄 လေသံကြိမ်နှုန်းများကို ဖြတ်ထုတ်နေပါသည်..."
        )

        extract_cmd = [
            "ffmpeg", "-y", "-i", input_file,
            "-vn",
            "-af", "highpass=f=220,lowpass=f=6000,afftdn=nr=30:nf=-40:tn=1",
            "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            "-loglevel", "error", raw_wav
        ]
        res = subprocess.run(extract_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"အသံဆွဲထုတ်၍ မရပါ:\n{res.stderr.strip()}")

        check_cancelled(chat_id)

        current_step = "Background Noise ဖယ်ရှားခြင်း"
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            f"🌪️ လေသံနှင့် အပြင်ဆူညံသံများကို ဖယ်ရှားနေပါသည်...\n{make_progress_bar(0)}"
        )

        reduce_audio_noise_advanced(
            raw_wav, clean_wav,
            chat_id=chat_id,
            msg_id=status_msg.message_id
        )

        check_cancelled(chat_id)

        current_step = "ဖိုင်ပြန်ပေါင်းခြင်းနှင့် ပေးပို့ခြင်း"
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            "📤 100% ပြီးစီးပါပြီ! သန့်စင်ပြီးသော ဖိုင်ကို ပြန်ပို့ပေးနေပါသည်..."
        )

        if content_type in ['video', 'video_note']:
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.mp4")
            merge_cmd = [
                "ffmpeg", "-y", "-i", input_file, "-i", clean_wav,
                "-c:v", "copy",
                "-af", PRO_WIND_FILTER,
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
                    caption="✅ လေသံနှင့် ဆူညံသံများကို အပြည့်အဝ ဖယ်ရှားပေးထားပါသည်။",
                    reply_to_message_id=message.message_id,
                    reply_markup=get_main_keyboard()
                )

        elif content_type == 'voice':
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.ogg")
            ogg_cmd = [
                "ffmpeg", "-y", "-i", clean_wav,
                "-af", PRO_WIND_FILTER,
                "-c:a", "libopus", "-b:a", "64k",
                "-loglevel", "error", output_file
            ]
            res = subprocess.run(ogg_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"Voice ပြောင်းရာတွင် အမှားဖြစ်ပွားပါသည်:\n{res.stderr.strip()}")

            with open(output_file, 'rb') as f:
                bot.send_voice(
                    chat_id, f,
                    caption="✅ လေသံနှင့် ဆူညံသံများကို အပြည့်အဝ ဖယ်ရှားပေးထားပါသည်။",
                    reply_to_message_id=message.message_id,
                    reply_markup=get_main_keyboard()
                )

        elif content_type == 'audio':
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.mp3")
            mp3_cmd = [
                "ffmpeg", "-y", "-i", clean_wav,
                "-af", PRO_WIND_FILTER,
                "-c:a", "libmp3lame", "-b:a", "192k",
                "-loglevel", "error", output_file
            ]
            res = subprocess.run(mp3_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"MP3 ပြောင်းရာတွင် အမှားဖြစ်ပွားပါသည်:\n{res.stderr.strip()}")

            with open(output_file, 'rb') as f:
                bot.send_audio(
                    chat_id, f,
                    caption="✅ လေသံနှင့် ဆူညံသံများကို အပြည့်အဝ ဖယ်ရှားပေးထားပါသည်။",
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
    while True:
        job = job_queue.get()
        try:
            process_single_job(job)
        except Exception as e:
            print(f"Worker Error: {e}")
        finally:
            job_queue.task_done()


@bot.message_handler(commands=['start', 'help'])
@bot.message_handler(func=lambda msg: msg.text == "Start 🔄")
def send_welcome(message):
    chat_id = message.chat.id
    with state_lock:
        cancelled_users.discard(chat_id)

    welcome_text = (
        "👋 မင်္ဂလာပါ! **Pro Wind & Noise Cleaner Bot** မှ ကြိုဆိုပါတယ်။\n\n"
        "📹 **Video ဖိုင်**\n"
        "🎵 **Audio (MP3/WAV) ဖိုင်**\n"
        "🎤 **Voice Message**\n\n"
        "ကြိုက်နှစ်သက်ရာ ပေးပို့နိုင်ပါသည်။ ပြင်းထန်သော လေသံများနှင့် ဆူညံသံများကို အပြတ်ရှင်းလင်းပေးမည်ဖြစ်ပြီး လုပ်ဆောင်နေစဉ် `%` တက်သွားမည် ဖြစ်ပါသည်။"
    )
    bot.send_message(
        chat_id,
        welcome_text,
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )


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
    Thread(target=queue_worker, daemon=True).start()
    print("🤖 Pro Wind & Noise Denoise Bot is polling...")
    bot.infinity_polling(timeout=60, long_polling_timeout=30)

