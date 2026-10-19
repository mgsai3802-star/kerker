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
import torch

from df.enhance import init_df, enhance, load_audio, save_audio

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

print("🤖 DeepFilterNet AI Model ကို စတင်ဖန်တီးနေပါသည်...")
ai_model, df_state, _ = init_df()
print("✅ AI Model အဆင်သင့် ဖြစ်ပါပြီ။")

app = Flask(__name__)

@app.route('/')
def health_check():
    return "AI Denoise Bot is running!", 200

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


def enhance_audio_with_progress(input_wav, output_wav, chat_id, msg_id):
    """AI Model ဖြင့် အသံကို အပိုင်းလိုက်ခွဲ၍ Real-time % တက်အောင် ပြုလုပ်ပေးသည့် Function"""
    sr = df_state.sr()
    audio_data, file_sr = sf.read(input_wav, dtype='float32')
    
    if file_sr != sr:
        # Sample rate မတူပါက FFmpeg ဖြင့် အရင်ချိန်ညှိပြီးသားမို့ ဒီမှာ တိုက်ရိုက်သုံးနိုင်သည်
        pass

    if audio_data.ndim > 1:
        audio_data = np.mean(audio_data, axis=1)

    total_samples = len(audio_data)
    if total_samples == 0:
        raise ValueError("အသံဖိုင်ထဲတွင် အသံဒေတာ လုံးဝမပါဝင်ပါ။")

    # AI Model အတွက် တစ်ကြိမ်လျှင် ၅ စက္ကန့်စာ အပိုင်းလိုက် ခွဲထုတ်မည်
    chunk_samples = sr * 5
    enhanced_chunks = []
    last_edit_time = 0

    total_chunks = (total_samples + chunk_samples - 1) // chunk_samples

    for i, start in enumerate(range(0, total_samples, chunk_samples)):
        check_cancelled(chat_id)
        end = min(start + chunk_samples, total_samples)
        
        chunk = audio_data[start:end]
        
        # PyTorch Tensor သို့ ပြောင်းလဲခြင်း
        tensor_chunk = torch.from_numpy(chunk).unsqueeze(0)
        
        # AI Enhancement ပြုလုပ်ခြင်း
        with torch.no_grad():
            enhanced_tensor = enhance(ai_model, df_state, tensor_chunk)
            
        enhanced_np = enhanced_tensor.squeeze(0).numpy()
        enhanced_chunks.append(enhanced_np)

        # ပြီးစီးမှု ရာခိုင်နှုန်း (% ) တွက်ချက်ခြင်း
        percent = int(((i + 1) / total_chunks) * 100)
        now = time.time()

        if now - last_edit_time >= 1.5 or percent == 100:
            bar_str = make_progress_bar(percent)
            safe_edit_message(
                chat_id,
                msg_id,
                f"🧠 AI ဖြင့် လေသံနှင့် ဆူညံသံများကို ဖယ်ရှားနေပါသည်...\n{bar_str}"
            )
            last_edit_time = now

    final_audio = np.concatenate(enhanced_chunks)
    sf.write(output_wav, final_audio, sr)

    del audio_data, enhanced_chunks, final_audio
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

        current_step = "FFmpeg ဖြင့် အသံထုတ်ယူခြင်း"
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            "🔄 AI ဖြင့် သန့်စင်ရန် အသံကို ပြင်ဆင်နေပါသည်..."
        )

        sr = df_state.sr()
        extract_cmd = [
            "ffmpeg", "-y", "-i", input_file,
            "-vn", "-acodec", "pcm_s16le", "-ar", str(sr), "-ac", "1",
            "-loglevel", "error", raw_wav
        ]
        res = subprocess.run(extract_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"အသံဆွဲထုတ်၍ မရပါ:\n{res.stderr.strip()}")

        check_cancelled(chat_id)

        current_step = "AI Denoise ပြုလုပ်ခြင်း"
        safe_edit_message(
            chat_id,
            status_msg.message_id,
            f"🧠 AI ဖြင့် လေသံနှင့် ဆူညံသံများကို ဖယ်ရှားနေပါသည်...\n{make_progress_bar(0)}"
        )

        enhance_audio_with_progress(
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
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-map", "0:v:0", "-map", "1:a:0", "-shortest",
                "-loglevel", "error", output_file
            ]
            res = subprocess.run(merge_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"ဗီဒီယိုပြန်ပေါင်းရာတွင် အမှားဖြစ်ပွားပါသည်:\n{res.stderr.strip()}")

            with open(output_file, 'rb') as f:
                bot.send_video(
                    chat_id, f,
                    caption="✨ DeepFilterNet AI ဖြင့် လေသံနှင့် ဆူညံသံများကို အပြည့်အဝ ဖယ်ရှားပေးထားပါသည်။",
                    reply_to_message_id=message.message_id,
                    reply_markup=get_main_keyboard()
                )

        elif content_type == 'voice':
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.ogg")
            ogg_cmd = [
                "ffmpeg", "-y", "-i", clean_wav,
                "-c:a", "libopus", "-b:a", "64k",
                "-loglevel", "error", output_file
            ]
            res = subprocess.run(ogg_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"Voice ပြောင်းရာတွင် အမှားဖြစ်ပွားပါသည်:\n{res.stderr.strip()}")

            with open(output_file, 'rb') as f:
                bot.send_voice(
                    chat_id, f,
                    caption="✨ DeepFilterNet AI ဖြင့် လေသံနှင့် ဆူညံသံများကို အပြည့်အဝ ဖယ်ရှားပေးထားပါသည်။",
                    reply_to_message_id=message.message_id,
                    reply_markup=get_main_keyboard()
                )

        elif content_type == 'audio':
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.mp3")
            mp3_cmd = [
                "ffmpeg", "-y", "-i", clean_wav,
                "-c:a", "libmp3lame", "-b:a", "192k",
                "-loglevel", "error", output_file
            ]
            res = subprocess.run(mp3_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"MP3 ပြောင်းရာတွင် အမှားဖြစ်ပွားပါသည်:\n{res.stderr.strip()}")

            with open(output_file, 'rb') as f:
                bot.send_audio(
                    chat_id, f,
                    caption="✨ DeepFilterNet AI ဖြင့် လေသံနှင့် ဆူညံသံများကို အပြည့်အဝ ဖယ်ရှားပေးထားပါသည်။",
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
        "👋 မင်္ဂလာပါ! **AI Denoise Bot (Live Progress %)** မှ ကြိုဆိုပါတယ်။\n\n"
        "📹 **Video ဖိုင်**\n"
        "🎵 **Audio (MP3/WAV) ဖိုင်**\n"
        "🎤 **Voice Message**\n\n"
        "ကြိုက်နှစ်သက်ရာ ပေးပို့နိုင်ပါသည်။ AI ဖြင့် လေသံများကို အပြတ်ရှင်းလင်းပေးမည်ဖြစ်ပြီး လုပ်ဆောင်နေစဉ် ရာခိုင်နှုန်း `%` တိုက်ရိုက်ပြသပေးပါမည်။"
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
            f"⏳ ရှေ့ကဖိုင်ကို AI ဖြင့် အရင်လုပ်ဆောင်နေပါသည်။\n"
            f"📋 သင့်ဖိုင်ကို အလှည့်ကျစာရင်း **(Queue #{waiting_count})** တွင် ထည့်သွင်းထားပါသည်...",
            parse_mode="Markdown"
        )
    else:
        status_msg = bot.reply_to(message, "📥 ဖိုင်ကို လက်ခံရရှိပါပြီ။ AI ဖြင့် စတင်သန့်စင်နေပါသည်...")

    job_queue.put({
        'message': message,
        'status_msg': status_msg
    })


if __name__ == "__main__":
    Thread(target=run_web_server, daemon=True).start()
    Thread(target=queue_worker, daemon=True).start()
    print("🤖 AI Denoise Bot with Live % is polling...")
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
    
