# Video Rendering Service

Python-based video rendering service that generates voiceover narration videos with synchronized subtitles. Built for low-resource VPS environments with efficient job queueing and background processing.

## Features

- **Text-to-Speech**: Piper TTS (CPU-only, en_US-lessac-medium voice)
- **Script Processing**: Intelligent sentence splitting with auto-merge/split
- **Subtitle Generation**: Sentence-synced ASS subtitles with timing from audio duration
- **Audio Mixing**: Voice + background music with volume balancing
- **Video Rendering**: FFmpeg-based rendering with burned subtitles
- **Cloud Storage**: Automatic upload to Backblaze B2 (S3-compatible)
- **Webhook Notifications**: Real-time progress updates
- **Job Queue**: Asynchronous processing with 3 concurrent job limit
- **Resource Optimized**: Designed for $4-$6 VPS with swap

## Quick Start

### Prerequisites

- Python 3.10+
- FFmpeg and ffprobe
- Piper TTS binary and model
- Backblaze B2 account

### Installation

1. **Install system dependencies** (Ubuntu/Debian):
```bash
sudo apt-get update
sudo apt-get install -y ffmpeg python3.10 python3-pip
```

2. **Install Piper TTS**:
```bash
# Download Piper from https://github.com/rhasspy/piper/releases
wget https://github.com/rhasspy/piper/releases/latest/download/piper_linux_x86_64.tar.gz
tar -xzf piper_linux_x86_64.tar.gz
sudo mv piper/piper /usr/local/bin/

# Download voice model
mkdir -p /usr/local/share/piper
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx \
  -O /usr/local/share/piper/en_US-lessac-medium.onnx
```

3. **Install Python dependencies**:
```bash
pip install -r requirements.txt
```

4. **Configure environment**:
```bash
cp .env.example .env
# Edit .env with your settings
```

Required environment variables:
```env
BACKBLAZE_BUCKET_NAME=your-bucket-name
BACKBLAZE_KEY_ID=your-key-id
BACKBLAZE_APPLICATION_KEY=your-application-key
BACKBLAZE_ENDPOINT_URL=https://s3.us-west-002.backblazeb2.com
WEBHOOK_URL=https://your-webhook-endpoint.com/notify
PIPER_MODEL_PATH=/usr/local/share/piper/en_US-lessac-medium.onnx
MAX_CONCURRENT_JOBS=3
```

### Running the Service

**Development mode**:
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Production mode**:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

## Docker

### Build and Run (Docker Desktop)

1. Create your `.env` file:
```bash
cp .env.example .env
# Edit .env with your settings
```

2. Build and run:
```bash
docker compose up --build
```

The API will be available at `http://localhost:8000`.

### Low-memory droplets (512 MiB)

1. Enable swap on the droplet (recommended 1–2 GB).
2. Keep concurrency at 1 (default in `docker-compose.yml`).
3. Start the service:
```bash
docker compose up --build -d
```

## API Usage

### Submit a Render Job

**POST** `/render`

```json
{
  "script": "Hello world. This is a test narration. Welcome to our video.",
  "base_video_url": "https://example.com/video.mp4",
  "bgm_url": "https://example.com/music.mp3",
  "settings": {
    "resolution": "1920x1080",
    "subtitle_style": {
      "font_name": "Arial",
      "font_size": 24
    }
  }
}
```

**Response**:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "voice_url": null,
  "subtitles_url": null,
  "video_url": null,
  "error": null
}
```

### Check Job Status

**GET** `/status/{job_id}`

**Response** (completed):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "voice_url": "https://s3.backblazeb2.com/bucket/uploads/voice/{job_id}/voice.wav",
  "subtitles_url": "https://s3.backblazeb2.com/bucket/uploads/subtitles/{job_id}/subs.ass",
  "video_url": "https://s3.backblazeb2.com/bucket/uploads/videos/{job_id}/final.mp4",
  "error": null
}
```

### Health Check

**GET** `/health`

```json
{
  "status": "healthy",
  "service": "video-rendering-service",
  "version": "1.0.0",
  "workers": {
    "active": 3,
    "max_concurrent_jobs": 3
  },
  "queue": {
    "queued": 2,
    "processing": 1
  }
}
```

## Webhook Events

The service sends webhook notifications to the configured `WEBHOOK_URL`:

### 1. Voiceover Uploaded
Sent after voice.wav is uploaded to S3.

```json
{
  "event": "voiceover_uploaded",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "voice_url": "https://s3.backblazeb2.com/...",
  "timestamp": "2025-01-12T20:30:45Z"
}
```

### 2. Video Completed
Sent after final video is rendered and uploaded.

```json
{
  "event": "video_completed",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "voice_url": "https://s3.backblazeb2.com/...",
  "subtitles_url": "https://s3.backblazeb2.com/...",
  "video_url": "https://s3.backblazeb2.com/...",
  "timestamp": "2025-01-12T20:35:12Z"
}
```

## Architecture

### Processing Pipeline

1. **Script Processing** → Split text into sentences (auto-merge < 5 words, auto-split > 20 words)
2. **TTS Generation** → Generate audio per sentence using Piper TTS → Concatenate to `voice.wav`
3. **S3 Upload** → Upload voice.wav → **Webhook: voiceover_uploaded**
4. **Subtitle Generation** → Measure audio durations with ffprobe → Generate ASS subtitles
5. **Audio Mixing** → Mix voice + BGM (BGM at 20% volume with fade-out)
6. **Video Rendering** → Burn subtitles into video with FFmpeg
7. **S3 Upload** → Upload subs.ass and final.mp4 → **Webhook: video_completed**
8. **Cleanup** → Delete temporary files from `/tmp/{job_id}/`

### Job Queue System

- **Asynchronous Processing**: Jobs queued immediately, processed by background workers
- **Concurrency Limit**: Max 3 jobs process simultaneously (configurable)
- **Job States**: `queued` → `processing` → `completed`/`failed`
- **In-Memory Queue**: Uses asyncio.Queue with Semaphore for concurrency control

### S3 Bucket Structure

```
bucket-name/
├── uploads/
│   ├── voice/{job_id}/voice.wav
│   ├── subtitles/{job_id}/subs.ass
│   └── videos/{job_id}/final.mp4
```

## Resource Limits

- **Max Script Length**: 50,000 characters
- **Max Video Size**: 500 MB
- **Max Audio Size**: 100 MB
- **Download Timeout**: 30 seconds
- **Webhook Timeout**: 5 seconds

## Deployment

### VPS Setup (Low-Resource)

1. **Enable swap** (for 1-2GB RAM VPS):
```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

2. **Run as systemd service**:

Create `/etc/systemd/system/video-render.service`:
```ini
[Unit]
Description=Video Rendering Service
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/video-render
Environment="PATH=/usr/local/bin:/usr/bin:/bin"
ExecStart=/usr/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable video-render
sudo systemctl start video-render
sudo systemctl status video-render
```

### Environment Variables

See `.env.example` for all configuration options.

## Development

### Project Structure

```
automation-python-server/
├── main.py                      # FastAPI app with worker lifecycle
├── config.py                    # Pydantic settings
├── requirements.txt             # Dependencies
└── src/
    ├── api/routes.py           # API endpoints
    ├── services/               # Core processing services
    │   ├── script_processor.py
    │   ├── tts_service.py
    │   ├── subtitle_service.py
    │   ├── audio_mixer.py
    │   ├── video_renderer.py
    │   └── webhook_service.py
    └── utils/                  # Utilities
        ├── s3_uploader.py
        ├── file_manager.py
        ├── job_manager.py
        ├── worker.py
        └── constants.py
```

### Key Components

- **Job Manager**: Queue and status tracking with asyncio.Queue
- **Worker**: Background job processor (3 workers by default)
- **Services**: Modular pipeline components (TTS, subtitles, rendering, etc.)
- **S3 Uploader**: Backblaze B2 integration with organized paths
- **Webhook Service**: Non-blocking notifications with timeout

## Troubleshooting

### Piper TTS Not Found
```bash
which piper
# Should output: /usr/local/bin/piper
# If not, check installation and PATH
```

### FFmpeg Issues
```bash
ffmpeg -version
ffprobe -version
# Ensure both are installed
```

### S3 Upload Failures
- Check Backblaze B2 credentials in `.env`
- Verify endpoint URL matches your bucket region
- Ensure bucket exists and is accessible

### Memory Issues
- Enable swap (see Deployment section)
- Reduce `MAX_CONCURRENT_JOBS` to 2 or 1
- Monitor with `htop` during processing

## License

MIT License

## Support

For issues and feature requests, please contact the development team.
