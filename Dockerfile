FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Piper TTS binary
RUN mkdir -p /tmp/piper \
    && wget -O /tmp/piper/piper.tar.gz https://github.com/rhasspy/piper/releases/latest/download/piper_linux_x86_64.tar.gz \
    && tar -xzf /tmp/piper/piper.tar.gz -C /tmp/piper \
    && mv /tmp/piper/piper /usr/local/bin/piper \
    && rm -rf /tmp/piper

# Install Piper voice model
RUN mkdir -p /usr/local/share/piper \
    && wget -O /usr/local/share/piper/en_US-lessac-medium.onnx \
        https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
