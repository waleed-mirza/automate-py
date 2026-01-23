# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python-based video rendering service that generates voiceover narration videos. Accepts a script and base video, produces TTS audio, synchronized ASS subtitles, and final rendered MP4.

**Target deployment**: Low-resource VPS ($4-$6 droplet) with 3 concurrent jobs max.

## Architecture

### Core Pipeline

1. **Script Processing**: Normalize and split raw text into sentences (auto-merge short, auto-split long)
2. **TTS Generation**: Use selected TTS provider (Piper or Kokoro, CPU-only) to generate audio per sentence → concatenate to `voice.wav`
3. **Subtitle Sync**: Use ffprobe to measure audio durations → generate sentence-level `.ass` subtitles (no Whisper)
4. **Audio Mixing**: Combine voice + background music (lower BGM volume, optional fade-out)
5. **Video Rendering**: FFmpeg burns ASS subtitles into base video with mixed audio
6. **Thumbnail**: Generate via configured provider (FFmpeg frame extraction or Cloudflare AI + Pillow text overlay)
7. **Upload**: Push artifacts (`voice.wav`, `subs.ass`, `final.mp4`, `thumbnail.jpg`) to Backblaze S3, return URLs

### Tech Stack

- **Python**: 3.10+
- **Web Framework**: FastAPI
- **TTS**: Piper or Kokoro (CPU-only, optimized for low-resource)
- **Video Processing**: FFmpeg + ffprobe
- **Storage**: Backblaze B2 (S3-compatible)
- **Concurrency**: Queue-based, max 3 jobs simultaneously

## API Design

### Input (HTTP endpoint)

- `script`: Raw text (unsplit)
- `base_video_url`: MP4 URL or `s3://bucket/key` location (service presigns for download)
- `bgm_url`: Optional background music (MP3/WAV) URL or `s3://bucket/key` (service presigns for download)
- Optional settings: subtitle style, pause duration, resolution

### Output (JSON)

URLs for uploaded artifacts: `voice.wav`, `subs.ass`, `final.mp4`, `thumbnail.jpg`

## Development Commands

### Setup

1. Install system dependencies:

   ```bash
   # Ubuntu/Debian
   sudo apt-get update
   sudo apt-get install -y ffmpeg python3.10 python3-pip

   # Install Piper TTS
   # Download from https://github.com/rhasspy/piper/releases
   # Place binary in /usr/local/bin/piper
   # Download en_US-lessac-medium model and place in /usr/local/share/piper/
   ```

2. Install Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Configure environment:
   ```bash
   cp .env.example .env
   # Edit .env with your Backblaze B2 credentials and webhook URL
   ```

### Running the Server

```bash
# Development mode with auto-reload
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Production mode
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

### Docker (Docker Desktop)

```bash
cp .env.example .env
docker compose up --build
```

Docker compose mounts the repo into `/app` and runs uvicorn with `--reload` for dev.

### Low-memory droplets (512 MiB)

- Enable swap (1–2 GB).
- Keep concurrency at 1 (default in docker-compose).
- Start with `docker compose up --build -d`.

### API Endpoints

- `GET /health` - Health check with queue metrics
- `POST /render` - Submit render job (returns immediately with job_id)
- `POST /voiceover` - Synchronous script -> TTS voiceover upload (returns S3 location)
- `POST /render-video` - Synchronous render using provided S3 voiceover + base video (+ optional BGM); returns S3 locations
- `GET /status/{job_id}` - Check job status and get URLs
- `GET /` - Service info and queue status

## Implementation Details

### Project Structure

```
automation-python-server/
├── main.py                      # FastAPI app with worker lifecycle
├── config.py                    # Pydantic settings from .env
├── requirements.txt             # Python dependencies
├── .env.example                 # Environment template
└── src/
    ├── api/
    │   └── routes.py           # API endpoints (render, status)
    ├── services/
    │   ├── script_processor.py # Sentence splitting logic
    │   ├── tts_service.py      # Piper/Kokoro TTS integration
    │   ├── subtitle_service.py # ASS subtitle generation
    │   ├── audio_mixer.py      # Voice + BGM mixing
    │   ├── video_renderer.py   # FFmpeg video rendering
    │   ├── thumbnail_service.py # Thumbnail provider router
    │   ├── ai_thumbnail_service.py # Cloudflare AI + Pillow thumbnails
    │   └── webhook_service.py  # Webhook notifications
    └── utils/
        ├── s3_uploader.py      # Backblaze B2 uploads
        ├── file_manager.py     # Temp file cleanup
        ├── job_manager.py      # Queue and status tracking
        ├── worker.py           # Background job processor
        └── constants.py        # Resource limits
```

### Webhooks

Two webhook events sent to configured `WEBHOOK_URL`:

1. **voiceover_uploaded** - After voice.wav uploaded to S3
2. **video_completed** - After final.mp4 uploaded to S3 (includes `thumbnail_url`, may be null)

Webhook failures are logged but don't block processing (5s timeout, no retries).

### Subtitle Timing Dependency

- Keep per-sentence WAVs on disk; `voice.wav` should be a copy, not a rename, so subtitle timing can ffprobe `sentence_###.wav` even for single-sentence scripts.
- `/render-manual` generates per-sentence TTS audio for subtitle timing, then uses the provided voiceover for the final mix; subtitles may drift if the provided voice pacing differs.


### S3 Bucket Organization

- Store and return S3 locations in `s3://bucket/key` format (never HTTPS URLs).
- Backblaze requires path-style addressing for S3 requests.
- Key format: `uploads/{folder}/{uuid}-{filename}`.

Folders:
- voiceovers -> generated TTS audio
- subtitles -> ASS subtitle files
- renders -> final rendered videos
- thumbnails -> thumbnail JPEGs

### Signed URLs

- Generate presigned URLs only on-demand (default 3600s) for HTTP access; never store them.
- Backblaze B2 presigned URLs must use SigV4; client is configured with `signature_version="s3v4"` and region inferred from the endpoint host when possible.

```
bucket-name/
+-- uploads/
    +-- voiceovers/{uuid}-voice.wav
    +-- subtitles/{uuid}-subs.ass
    +-- renders/{uuid}-final.mp4
    +-- thumbnails/{uuid}-thumbnail.jpg
```

### Job States

- `queued` - Job added to queue, waiting for worker
- `processing` - Worker is processing the job
- `completed` - All steps successful, URLs available
- `failed` - Error occurred, error message in response

### Piper Executable

- `PIPER_BIN_PATH` env var overrides the Piper binary path (default `/usr/local/bin/piper`); service verifies the binary is executable at startup.
- Piper binary requires `libespeak-ng1` runtime library in the container/host.

### TTS Provider Selection

- `TTS_PROVIDER` chooses between `piper` and `kokoro` (default `kokoro`).
- Kokoro uses `KOKORO_MODEL_PATH` (default `/usr/local/share/kokoro/kokoro-v1.0.onnx`) and `KOKORO_VOICES_PATH` (default `/usr/local/share/kokoro/voices/voices-v1.0.bin`).
- Kokoro requires `libsndfile1` for WAV output.

### Thumbnail Provider Selection

- `THUMBNAIL_PROVIDER` chooses between `frame` and `cloudflare` (default `frame`).
- **frame**: FFmpeg extracts a frame at ~10% video duration. Free, fast, no external dependencies.
- **cloudflare**: Cloudflare Workers AI (Flux.1 Schnell) generates eye-catching background + Pillow adds text overlay.

Cloudflare AI thumbnail features:
- Generates background image from script context via Flux.1 Schnell model
- Extracts 2-4 word hook from script for text overlay
- Bold white text with black outline (YouTube best practices)
- Auto-resizes to YouTube-optimal dimensions based on video aspect ratio:
  - 16:9 → 1280x720
  - 9:16 → 720x1280
  - 1:1 → 1080x1080
- Free tier: ~50-100 images/day (10,000 neurons)
- Fallback: If Cloudflare API fails or credentials missing, falls back to frame extraction

Required env vars for Cloudflare provider:
- `CLOUDFLARE_ACCOUNT_ID`: From Cloudflare Dashboard → Workers & Pages → right sidebar
- `CLOUDFLARE_API_TOKEN`: Create at My Profile → API Tokens → "Workers AI" template

## Critical Constraints

- **Resource-limited**: Must work on low-RAM VPS with swap enabled
- **No GPU**: Piper TTS runs CPU-only
- **Cost-optimized**: Minimize processing time and storage costs
- **Queue management**: Handle max 3 concurrent jobs to prevent OOM
- **Subtitle timing**: Calculate from actual ffprobe durations, NOT transcription alignment
- **Async processing**: Jobs queued immediately, processed in background by 3 workers

## Persistent Memory Directive (DO NOT MODIFY)

This file is the authoritative, long-term memory of the project.
Do not re-discover or re-analyze information already recorded here.
Whenever you learn something durable, non-obvious, or repeatedly needed, update this file immediately.
Persist changes to architecture, workflows, conventions, feature behavior, and integrations.
Write only concise, factual summaries - no code, no speculation.
If unsure whether something belongs here, include it.
Before completing any task, check whether this file should be updated.
Assume future sessions depend entirely on this file for project understanding.
