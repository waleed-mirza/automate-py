# CLAUDE.md

This file provides guidance to Codex when working with code in this repository.

## Project Overview

Python-based video rendering service that generates voiceover narration videos. Accepts a script and base video, produces TTS audio, synchronized ASS subtitles, and final rendered MP4.

**Target deployment**: Low-resource VPS ($4-$6 droplet) with 3 concurrent jobs max.

## Architecture

### Core Pipeline

1. **Script Processing**: Normalize and split raw text into sentences (auto-merge short, auto-split long)
2. **TTS Generation**: Use Piper TTS (CPU-only) to generate audio per sentence → concatenate to `voice.wav`
3. **Subtitle Sync**: Use ffprobe to measure audio durations → generate sentence-level `.ass` subtitles (no Whisper)
4. **Audio Mixing**: Combine voice + background music (lower BGM volume, optional fade-out)
5. **Video Rendering**: FFmpeg burns ASS subtitles into base video with mixed audio
6. **Upload**: Push artifacts (`voice.wav`, `subs.ass`, `final.mp4`) to Backblaze S3, return URLs

### Tech Stack

- **Python**: 3.10+
- **Web Framework**: FastAPI
- **TTS**: Piper (CPU-only, optimized for low-resource)
- **Video Processing**: FFmpeg + ffprobe
- **Storage**: Backblaze B2 (S3-compatible)
- **Concurrency**: Queue-based, max 3 jobs simultaneously

## API Design

### Input (HTTP endpoint)

- `script`: Raw text (unsplit)
- `base_video_url`: MP4 URL
- `bgm_url`: Optional background music (MP3/WAV)
- Optional settings: subtitle style, pause duration, resolution

### Output (JSON)

URLs for uploaded artifacts: `voice.wav`, `subs.ass`, `final.mp4`

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

### Low-memory droplets (512 MiB)

- Enable swap (1–2 GB).
- Keep concurrency at 1 (default in docker-compose).
- Start with `docker compose up --build -d`.

### API Endpoints

- `GET /health` - Health check with queue metrics
- `POST /render` - Submit render job (returns immediately with job_id)
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
    │   ├── tts_service.py      # Piper TTS integration
    │   ├── subtitle_service.py # ASS subtitle generation
    │   ├── audio_mixer.py      # Voice + BGM mixing
    │   ├── video_renderer.py   # FFmpeg video rendering
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
2. **video_completed** - After final.mp4 uploaded to S3

Webhook failures are logged but don't block processing (5s timeout, no retries).

### S3 Bucket Organization

```
bucket-name/
├── uploads/voiceovers/{job_id}/voice.wav
├── uploads/subtitles/{job_id}/subs.ass
└── uploads/renders/{job_id}/final.mp4
```

### Job States

- `queued` - Job added to queue, waiting for worker
- `processing` - Worker is processing the job
- `completed` - All steps successful, URLs available
- `failed` - Error occurred, error message in response

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
