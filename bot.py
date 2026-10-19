import os
import gc
import time
import uuid
import traceback
import subprocess
from threading import Thread
from flask import Flask
import numpy as np
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
import soundfile as sf
import noisereduce as nr

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN မရှိပါ! Render Environment Variables တွင် ထည့်သွင်းပေးပါ။")

# Cancel ခလုတ်ကို လုပ်ဆောင်နေစဉ် ချက်ချင်းနှိပ်၍ရစေရန် threaded=True သုံးထားသည်
bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=4)

TEMP_DIR = "temp_files"
os.makedirs(TEMP_DIR, exist_ok=True)

# User တစ်ယောက်ချင်းစီ၏ လုပ်ဆောင်မှုနှင့် Cancel အခြေအနေကို မှတ်သားရန်
active_jobs = {}
cancel_flags = {}

# လူ့စကားပြောသံ (2.5kHz) ကို သီးသန့်ဆွဲတင်ပေးပြီး အသံတိုးနေသည့်နေရာများကို အလိုအလျောက် ချဲ့ပေးသည့် Filter
VOICE_BOOST_FILTER = (
    "highpass=f=100,"
    "lowpass=f=7500,"
    "equalizer=f=2500:t=q:w=1.2:g=6,"
    "dynaudnorm=f=150:g=15:p=0.95"
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
    """User က Cancel နှိပ်လိုက်သည့်အခါ ဖမ်းယူမည့် Custom Exception"""
    pass


def get_main_keyboard():
    """Start 🔄 နှင့် Cancel ❌ ပါဝင်သော Reply Keyboard တည်ဆောက်ခြင်း"""
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_start = KeyboardButton("Start 🔄")
    btn_cancel = KeyboardButton("Cancel ❌")
    markup.add(btn_start, btn_cancel)
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
    if cancel_flags.get(chat_id, False):
        raise CancelledError("လုပ်ဆောင်မှုကို ရပ်တန့်လိုက်ပါပြီ။")


def reduce_audio_noise_with_progress(input_wav, output_wav, chat_id, msg_id, prop_decrease=0.80):
    """Noise ဖျောက်ပြီး လူစကားသံကို ကြည်လင်အောင် ချိန်ညှိပေးသည့် Function"""
    audio_data, sample_rate = sf.read(input_wav, dtype='float32')

    if audio_data.ndim > 1:
        audio_data = np.mean(audio_data, axis=1)

    total_samples = len(audio_data)
    if total_samples == 0:
        raise ValueError("အသံဖိုင်ထဲတွင် အသံဒေတာ လုံးဝမပါဝင်ပါ။")

    chunk_size = max(sample_rate * 2, min(sample_rate * 8, total_samples // 5))
    pad = int(sample_rate * 0.2)
    noise_clip = audio_data[:min(total_samples, sample_rate)]

    cleaned_chunks = []
    last_edit_time = 0

    for start in range(0, total_samples, chunk_size):
        # အပိုင်းတစ်ပိုင်းလုပ်တိုင်း Cancel နှိပ်ထားခြင်း ရှိ/မရှိ စစ်ဆေးမည်
        check_cancelled(chat_id)

        end = min(start + chunk_size, total_samples)
        pad_start = max(0, start - pad)
        pad_end = min(total_samples, end + pad)

        chunk = audio_data[pad_start:pad_end]

        cleaned_chunk = nr.reduce_noise(
            y=chunk,
            sr=sample_rate,
            y_noise=noise_clip,
            stationary=False,
            prop_decrease=prop_decrease,
            n_fft=1024,
            n_jobs=1
        )

        trim_left = start - pad_start
        trim_right = len(cleaned_chunk) - (pad_end - end)
        cleaned_chunks.append(cleaned_chunk[trim_left:trim_right])

        percent = int((end / total_samples) * 100)
        now = time.time()

        if now - last_edit_time >= 2.0 or percent == 100:
            bar_str = make_progress_bar(percent)
            safe_edit_message(
                chat_id,
                msg_id,
                f"🎧 Background Noise ဖယ်ရှားပြီး လူသံမြှင့်တင်နေပါသည်...\n{bar_str}"
            )
            last_edit_time = now

    final_audio = np.concatenate(cleaned_chunks)

    # လူစကားသံ ပိုကျယ်လာစေရန် Peak Normalization ပြုလုပ်ခြင်း
    max_val = np.max(np.abs(final_audio))
    if max_val > 0:
        final_audio = (final_audio / max_val) * 0.95

    sf.write(output_wav, final_audio, sample_rate)

    del audio_data, cleaned_chunks, final_audio
    gc.collect()


# --- Start 🔄 နှင့် /start Command Handler ---
@bot.message_handler(commands=['start', 'help'])
@bot.message_handler(func=lambda msg: msg.text == "Start 🔄")
def send_welcome(message):
    chat_id = message.chat.id
    # လက်ရှိလုပ်နေသော အလုပ်ရှိပါက တစ်ပါတည်း ရပ်တန့်ပေးမည်
    if active_jobs.get(chat_id, False):
        cancel_flags[chat_id] = True

    welcome_text = (
        "👋 မင်္ဂလာပါ! **Voice Boost & Noise Remover Bot** မှ ကြိုဆိုပါတယ်။\n\n"
        "📹 **Video ဖိုင်**\n"
        "🎵 **Audio (MP3/WAV) ဖိုင်**\n"
        "🎤 **Voice Message**\n\n"
        "ကြိုက်နှစ်သက်ရာ ပေးပို့နိုင်ပါတယ်။ အပြင်ဆူညံသံများကို ဖယ်ရှားပြီး **လူစကားပြောသံကို ပိုကျယ်၊ ပိုကြည်လင်အောင်** ပြုလုပ်ပေးပါမယ်။\n\n"
        "💡 *လုပ်ဆောင်နေစဉ် ရပ်တန့်လိုပါက အောက်ပါ **Cancel ❌** ခလုတ်ကို နှိပ်နိုင်ပါသည်။*"
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
    chat_id = message.chat.id
    if active_jobs.get(chat_id, False):
        cancel_flags[chat_id] = True
        bot.reply_to(
            message,
            "🛑 လုပ်ဆောင်နေမှုကို ရပ်တန့်နေပါသည်... ခဏစောင့်ပါ။",
            reply_markup=get_main_keyboard()
        )
    else:
        bot.reply_to(
            message,
            "ℹ️ လက်ရှိတွင် လုပ်ဆောင်နေသော ဖိုင်မရှိပါ။ ဖိုင်အသစ် ပေးပို့နိုင်ပါသည်။",
            reply_markup=get_main_keyboard()
        )


# --- Video / Audio / Voice Handler ---
@bot.message_handler(content_types=['video', 'audio', 'voice', 'video_note'])
def handle_media(message):
    chat_id = message.chat.id

    if active_jobs.get(chat_id, False):
        bot.reply_to(
            message,
            "⚠️ ရှေ့ကဖိုင်ကို လုပ်ဆောင်နေဆဲ ဖြစ်ပါသည်။ ရပ်တန့်လိုပါက **Cancel ❌** ကို နှိပ်ပါ။",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        return

    content_type = message.content_type
    media = getattr(message, content_type)

    if media.file_size and media.file_size > 20 * 1024 * 1024:
        bot.reply_to(
            message,
            "⚠️ ဖိုင်အရွယ်အစား 20MB ထက် ကျော်လွန်နေသဖြင့် ဒေါင်းလုဒ်ဆွဲ၍ မရနိုင်ပါ။",
            reply_markup=get_main_keyboard()
        )
        return

    active_jobs[chat_id] = True
    cancel_flags[chat_id] = False

    status_msg = bot.reply_to(message, "📥 ဖိုင်ကို ဒေါင်းလုဒ်ဆွဲနေပါသည်... (0%)")

    uid = f"{chat_id}_{uuid.uuid4().hex[:8]}"
    input_file = os.path.join(TEMP_DIR, f"{uid}_input")
    raw_wav = os.path.join(TEMP_DIR, f"{uid}_raw.wav")
    clean_wav = os.path.join(TEMP_DIR, f"{uid}_clean.wav")
    output_file = ""
    current_step = "ဖိုင်ဒေါင်းလုဒ်ဆွဲခြင်း"

    try:
        # ၁။ ဖိုင်ဒေါင်းလုဒ်ဆွဲခြင်း
        file_info = bot.get_file(media.file_id)
        check_cancelled(chat_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open(input_file, 'wb') as f:
            f.write(downloaded_file)

        check_cancelled(chat_id)

        # ၂။ မူရင်းဖိုင်ထဲမှ အသံကို 24kHz WAV အဖြစ် ဆွဲထုတ်ပြီး အကြမ်းဖျင်း Noise ဖြတ်ခြင်း
        current_step = "FFmpeg ဖြင့် အသံလမ်းကြောင်း ဆွဲထုတ်ခြင်း"
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            "🔄 ဖိုင်ထဲမှ လူစကားပြောသံ လမ်းကြောင်းကို ဆွဲထုတ်နေပါသည်..."
        )

        extract_cmd = [
            "ffmpeg", "-y", "-i", input_file,
            "-vn", "-af", "highpass=f=100,lowpass=f=7500",
            "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1",
            "-loglevel", "error", raw_wav
        ]
        res = subprocess.run(extract_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"အသံဆွဲထုတ်၍ မရပါ (အသံမပါသော ဗီဒီယို ဖြစ်နိုင်ပါသည်)\nDetail: {res.stderr.strip()}")

        check_cancelled(chat_id)

        # ၃။ Noise ဖျောက်ခြင်း (Real-time % Progress Bar ဖြင့်)
        current_step = "Background Noise ဖယ်ရှားခြင်းနှင့် လူသံမြှင့်တင်ခြင်း"
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            f"🎧 Background Noise ဖယ်ရှားပြီး လူသံမြှင့်တင်နေပါသည်...\n{make_progress_bar(0)}"
        )

        reduce_audio_noise_with_progress(
            raw_wav, clean_wav,
            chat_id=chat_id,
            msg_id=status_msg.message_id,
            prop_decrease=0.80
        )

        check_cancelled(chat_id)

        # ၄။ Vocal EQ + Voice Boost Filter ထည့်၍ မူရင်း Format အတိုင်း ပြန်ပို့ခြင်း
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
                "-af", VOICE_BOOST_FILTER,
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
                    caption="✅ Background Noise ဖယ်ရှားပြီး လူစကားသံကို မြှင့်တင်ပေးထားပါသည်။",
                    reply_to_message_id=message.message_id,
                    reply_markup=get_main_keyboard()
                )

        elif content_type == 'voice':
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.ogg")
            ogg_cmd = [
                "ffmpeg", "-y", "-i", clean_wav,
                "-af", VOICE_BOOST_FILTER,
                "-c:a", "libopus", "-b:a", "64k",
                "-loglevel", "error", output_file
            ]
            res = subprocess.run(ogg_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"Voice ပြောင်းရာတွင် အမှားဖြစ်ပွားပါသည်:\n{res.stderr.strip()}")

            with open(output_file, 'rb') as f:
                bot.send_voice(
                    chat_id, f,
                    caption="✅ Background Noise ဖယ်ရှားပြီး လူစကားသံကို မြှင့်တင်ပေးထားပါသည်။",
                    reply_to_message_id=message.message_id,
                    reply_markup=get_main_keyboard()
                )

        elif content_type == 'audio':
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.mp3")
            mp3_cmd = [
                "ffmpeg", "-y", "-i", clean_wav,
                "-af", VOICE_BOOST_FILTER,
                "-c:a", "libmp3lame", "-b:a", "160k",
                "-loglevel", "error", output_file
            ]
            res = subprocess.run(mp3_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"MP3 ပြောင်းရာတွင် အမှားဖြစ်ပွားပါသည်:\n{res.stderr.strip()}")

            with open(output_file, 'rb') as f:
                bot.send_audio(
                    chat_id, f,
                    caption="✅ Background Noise ဖယ်ရှားပြီး လူစကားသံကို မြှင့်တင်ပေးထားပါသည်။",
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
        # အလုပ်ပြီးဆုံးကြောင်း သတ်မှတ်ပြီး ကြားခံဖိုင်အားလုံးကို ရှင်းလင်းမည်
        active_jobs[chat_id] = False
        cancel_flags[chat_id] = False
        for path in [input_file, raw_wav, clean_wav, output_file]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        gc.collect()


if __name__ == "__main__":
    Thread(target=run_web_server, daemon=True).start()
    print("🤖 Voice Boost & Denoise Bot is polling...")
    bot.infinity_polling(timeout=60, long_polling_timeout=30)

