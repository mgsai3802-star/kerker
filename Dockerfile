FROM python:3.10-slim

# FFmpeg၊ Audio Library နှင့် DeepFilterNet3 Rust AI Engine တို့ကို သွင်းခြင်း
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    curl \
    && curl -L https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-x86_64-unknown-linux-musl -o /usr/local/bin/deep-filter \
    && chmod +x /usr/local/bin/deep-filter \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]

