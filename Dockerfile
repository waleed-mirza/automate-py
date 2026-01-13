FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg wget ca-certificates libespeak-ng1 espeak-ng-data libsndfile1 \
    && mkdir -p /usr/share/espeak-ng-data \
    && if [ -f /usr/share/espeak-ng-data/phontab ]; then true; \
       else \
         found_dir="$(find /usr -type f -path '*/espeak-ng-data/phontab' -print -quit 2>/dev/null | xargs -r dirname)"; \
         if [ -n "$found_dir" ]; then \
           ln -s "$found_dir"/* /usr/share/espeak-ng-data/; \
         fi; \
       fi \
    && rm -rf /var/lib/apt/lists/*

# Install Piper TTS binary
RUN mkdir -p /tmp/piper \
    && wget -O /tmp/piper/piper.tar.gz https://github.com/rhasspy/piper/releases/latest/download/piper_linux_x86_64.tar.gz \
    && tar -xzf /tmp/piper/piper.tar.gz -C /tmp/piper \
    && mv /tmp/piper/piper/piper /usr/local/bin/piper \
    && find /tmp/piper -type f -name '*.so*' -exec mv {} /usr/local/lib/ \; || true \
    && ldconfig \
    && chmod +x /usr/local/bin/piper \
    && rm -rf /tmp/piper

# Install Piper voice model
RUN mkdir -p /usr/local/share/piper \
    && wget -O /usr/local/share/piper/en_US-lessac-medium.onnx \
        https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx \
    && wget -O /usr/local/share/piper/en_US-lessac-medium.onnx.json \
        https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json

# Install Kokoro model files
RUN mkdir -p /usr/local/share/kokoro/voices \
    && wget -O /usr/local/share/kokoro/kokoro-v1.0.onnx \
        https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx \
    && wget -O /usr/local/share/kokoro/voices/voices-v1.0.bin \
        https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
