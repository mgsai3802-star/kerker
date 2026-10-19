import os
import gc
import uuid
import subprocess
from threading import Thread
from flask import Flask
import telebot
import librosa
import soundfile as sf
import noisereduce as nr

# Render ၏ Environment Variable မှ Token ကို ဖတ်ယူမည်
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN မရှိပါ! Render Environment Variables တွင် ထည့်သွင်းပေးပါ။")

bot = telebot.TeleBot(BOT_TOKEN)

TEMP_DIR = "temp_files"
os.makedirs(TEMP_DIR, exist_ok=True)

# --- Render Web Service အတွက် Fake Web Server ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Denoise Bot is running!", 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def reduce_audio_noise(input_wav, output_wav, prop_decrease=0.85):
    """RAM 512MB တွင် အဆင်ပြေစေရန် Memory ချွေတာ၍ Noise ဖျောက်ပေးသည့် Function"""
    # sr=22050 သတ်မှတ်ခြင်းဖြင့် RAM အသုံးပြုမှုကို ထက်ဝက်ခန့် လျှော့ချပေးသည်
    audio_data, sample_rate = librosa.load(input_wav, sr=22050, mono=True)
    reduced_noise = nr.reduce_noise(
        y=audio_data,
        sr=sample_rate,
        stationary=False,
        prop_decrease=prop_decrease,
        n_jobs=1
    )
    sf.write(output_wav, reduced_noise, sample_rate)
    
    # RAM ပြန်ရှင်းခြင်း
    del audio_data, reduced_noise
    gc.collect()


@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "👋 မင်္ဂလာပါ! Background Noise Remover Bot မှ ကြိုဆိုပါတယ်။\n\n"
        "📹 **Video ဖိုင်**\n"
        "🎵 **Audio (MP3/WAV) ဖိုင်**\n"
        "🎤 **Voice Message**\n\n"
        "ကြိုက်နှစ်သက်ရာ ပေးပို့နိုင်ပါတယ်။ အပြင်ဆူညံသံများကို သန့်စင်ပြီး ပြန်ပို့ပေးပါမယ်။"
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown")


@bot.message_handler(content_types=['video', 'audio', 'voice', 'video_note'])
def handle_media(message):
    content_type = message.content_type
    media = getattr(message, content_type)

    if media.file_size and media.file_size > 20 * 1024 * 1024:
        bot.reply_to(message, "⚠️ ဖိုင်အရွယ်အစား 20MB အောက်သာ ပေးပို့နိုင်ပါသည်။")
        return

    status_msg = bot.reply_to(message, "⏳ ဖိုင်ကို ဒေါင်းလုဒ်ဆွဲနေပါသည်...")

    uid = f"{message.chat.id}_{uuid.uuid4().hex[:8]}"
    input_file = os.path.join(TEMP_DIR, f"{uid}_input")
    raw_wav = os.path.join(TEMP_DIR, f"{uid}_raw.wav")
    clean_wav = os.path.join(TEMP_DIR, f"{uid}_clean.wav")
    output_file = ""

    try:
        file_info = bot.get_file(media.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        with open(input_file, 'wb') as f:
            f.write(downloaded_file)

        bot.edit_message_text(
            "🎧 Background Noise ဖယ်ရှားနေပါသည်...",
            chat_id=message.chat.id,
            message_id=status_msg.message_id
        )

        extract_cmd = [
            "ffmpeg", "-y", "-i", input_file,
            "-vn", "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
            "-loglevel", "error", raw_wav
        ]
        subprocess.run(extract_cmd, check=True)

        reduce_audio_noise(raw_wav, clean_wav, prop_decrease=0.85)

        bot.edit_message_text(
            "📤 သန့်စင်ပြီးသော ဖိုင်ကို ပြန်ပို့ပေးနေပါသည်...",
            chat_id=message.chat.id,
            message_id=status_msg.message_id
        )

        if content_type in ['video', 'video_note']:
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.mp4")
            merge_cmd = [
                "ffmpeg", "-y", "-i", input_file, "-i", clean_wav,
                "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                "-map", "0:v:0", "-map", "1:a:0", "-shortest",
                "-loglevel", "error", output_file
            ]
            subprocess.run(merge_cmd, check=True)
            with open(output_file, 'rb') as f:
                bot.send_video(message.chat.id, f, caption="✅ Noise ဖယ်ရှားပြီးပါပြီ။", reply_to_message_id=message.message_id)

        elif content_type == 'voice':
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.ogg")
            ogg_cmd = [
                "ffmpeg", "-y", "-i", clean_wav,
                "-c:a", "libopus", "-b:a", "64k", "-loglevel", "error", output_file
            ]
            subprocess.run(ogg_cmd, check=True)
            with open(output_file, 'rb') as f:
                bot.send_voice(message.chat.id, f, caption="✅ Noise ဖယ်ရှားပြီးပါပြီ။", reply_to_message_id=message.message_id)

        elif content_type == 'audio':
            output_file = os.path.join(TEMP_DIR, f"{uid}_out.mp3")
            mp3_cmd = [
                "ffmpeg", "-y", "-i", clean_wav,
                "-c:a", "libmp3lame", "-b:a", "128k", "-loglevel", "error", output_file
            ]
            subprocess.run(mp3_cmd, check=True)
            with open(output_file, 'rb') as f:
                bot.send_audio(message.chat.id, f, caption="✅ Noise ဖယ်ရှားပြီးပါပြီ။", reply_to_message_id=message.message_id)

        bot.delete_message(message.chat.id, status_msg.message_id)

    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)}", chat_id=message.chat.id, message_id=status_msg.message_id)
    finally:
        for path in [input_file, raw_wav, clean_wav, output_file]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        gc.collect()


if __name__ == "__main__":
    # Flask server ကို Background Thread ဖြင့် စတင်မည်
    Thread(target=run_web_server, daemon=True).start()
    print("🤖 Denoise Bot is polling...")
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
    
