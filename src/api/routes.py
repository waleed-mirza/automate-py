from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import uuid
import logging
from pathlib import Path
import tempfile
import subprocess
import httpx

from src.utils.job_manager import get_job_manager, RenderJob
from src.utils.constants import (
    MAX_SCRIPT_LENGTH,
    MAX_SENTENCE_COUNT,
    MAX_AUDIO_SIZE_MB,
    DOWNLOAD_TIMEOUT_SECONDS,
    CLEANUP_ON_SUCCESS,
    CLEANUP_ON_FAILURE,
)
from src.services.script_processor import script_processor
from src.services.tts_service import tts_service
from src.services.subtitle_service import subtitle_service
from src.services.audio_mixer import audio_mixer
from src.services.video_renderer import video_renderer
from src.services.thumbnail_service import thumbnail_service
from src.utils.s3_uploader import s3_uploader
from src.utils.file_manager import file_manager

logger = logging.getLogger(__name__)
router = APIRouter()


class RenderSettings(BaseModel):
    """Optional rendering settings"""
    subtitle_style: Optional[dict] = None
    pause_duration: float = 0.5
    resolution: Optional[str] = None


class RenderRequest(BaseModel):
    """Request model for /render endpoint"""
    script: str = Field(..., description="Raw text script to process")
    base_video_url: str = Field(
        ...,
        description="Base video URL (HTTP/HTTPS or s3://bucket/key)"
    )
    bgm_url: Optional[str] = Field(
        None,
        description="Optional background music URL (HTTP/HTTPS or s3://bucket/key)"
    )
    title: Optional[str] = Field(
        None,
        description="Topic title for AI thumbnail text overlay"
    )
    settings: Optional[RenderSettings] = None


class VoiceoverRequest(BaseModel):
    """Request model for /voiceover endpoint"""
    script: str = Field(..., description="Raw text script to process")


class VoiceoverResponse(BaseModel):
    """Response model for /voiceover endpoint"""
    job_id: str
    status: str
    voice_url: Optional[str] = None
    error: Optional[str] = None


class ManualRenderRequest(BaseModel):
    """Request model for /render-video endpoint"""
    script: str = Field(..., description="Raw text script to process")
    voiceover_url: str = Field(
        ...,
        description="Voiceover S3 location (s3://bucket/key)"
    )
    base_video_url: str = Field(
        ...,
        description="Base video S3 location (s3://bucket/key)"
    )
    bgm_url: Optional[str] = Field(
        None,
        description="Optional background music S3 location (s3://bucket/key)"
    )
    is_short: bool = Field(
        False,
        description="Whether this is a YouTube Short (triggers thumbnail baking)"
    )
    thumbnail_url: Optional[str] = Field(
        None,
        description="Optional thumbnail S3 location to bake into video for Shorts"
    )
    settings: Optional[RenderSettings] = None


class RenderResponse(BaseModel):
    """Response model for /render endpoint"""
    job_id: str
    status: str
    voice_url: Optional[str] = None
    subtitles_url: Optional[str] = None
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    error: Optional[str] = None


def _validate_url_field(url: str, field_name: str):
    if not url.startswith(("http://", "https://", "s3://")):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be HTTP/HTTPS or s3://bucket/key"
        )

def _validate_s3_field(url: str, field_name: str):
    if not url.startswith("s3://"):
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be s3://bucket/key"
        )


async def _download_audio_file(url: str, job_dir: Path, filename_stem: str) -> Path:
    resolved_url = url
    if s3_uploader.is_s3_location(url):
        resolved_url = s3_uploader.get_presigned_url(url)

    extension = ".wav"
    if "." in url.split("/")[-1]:
        ext = url.split(".")[-1].split("?")[0]
        if ext.lower() in ["wav", "mp3", "aac", "m4a", "flac", "ogg"]:
            extension = "." + ext.lower()

    audio_file = job_dir / f"{filename_stem}{extension}"
    max_bytes = MAX_AUDIO_SIZE_MB * 1024 * 1024
    downloaded_bytes = 0

    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS) as client:
            async with client.stream("GET", resolved_url) as response:
                response.raise_for_status()

                with open(audio_file, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        downloaded_bytes += len(chunk)
                        if downloaded_bytes > max_bytes:
                            raise RuntimeError(
                                f"Audio file exceeds limit of {MAX_AUDIO_SIZE_MB} MB"
                            )
                        f.write(chunk)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Failed to download audio: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"Error downloading audio: {str(e)}")

    return audio_file


def _ensure_wav(input_file: Path, output_file: Path) -> Path:
    if input_file.suffix.lower() == ".wav":
        if input_file != output_file:
            input_file.replace(output_file)
            return output_file
        return input_file

    try:
        cmd = [
            "ffmpeg",
            "-i", str(input_file),
            "-acodec", "pcm_s16le",
            "-ar", "44100",
            str(output_file),
            "-y"
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"FFmpeg audio conversion failed: {e.stderr}")
    except Exception as e:
        raise RuntimeError(f"Failed to convert audio to WAV: {str(e)}")

    input_file.unlink(missing_ok=True)
    return output_file


async def _download_thumbnail_file(url: str, job_dir: Path) -> Path:
    """Download thumbnail image from S3 for baking into Shorts video."""
    resolved_url = url
    if s3_uploader.is_s3_location(url):
        resolved_url = s3_uploader.get_presigned_url(url)

    # Determine extension from URL
    extension = ".jpg"
    if "." in url.split("/")[-1]:
        ext = url.split(".")[-1].split("?")[0].lower()
        if ext in ["jpg", "jpeg", "png", "webp"]:
            extension = "." + ext

    thumbnail_file = job_dir / f"thumbnail_input{extension}"
    max_bytes = 10 * 1024 * 1024  # 10MB limit for thumbnails

    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS) as client:
            async with client.stream("GET", resolved_url) as response:
                response.raise_for_status()
                downloaded_bytes = 0
                with open(thumbnail_file, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        downloaded_bytes += len(chunk)
                        if downloaded_bytes > max_bytes:
                            raise RuntimeError("Thumbnail exceeds 10MB limit")
                        f.write(chunk)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Failed to download thumbnail: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"Error downloading thumbnail: {str(e)}")

    return thumbnail_file


@router.post("/render", response_model=RenderResponse)
async def render_video(request: RenderRequest):
    """
    Render a video with voiceover and subtitles.

    This endpoint queues a job for background processing and returns immediately.
    Use the /status/{job_id} endpoint to check job progress.

    Phase 5: Production ready with validation
    """
    job_id = str(uuid.uuid4())
    logger.info(f"Queueing render job: {job_id}")

    # Validate script length
    if len(request.script) > MAX_SCRIPT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Script too long. Maximum {MAX_SCRIPT_LENGTH} characters allowed."
        )

    if len(request.script.strip()) == 0:
        raise HTTPException(status_code=400, detail="Script cannot be empty")

    # Validate URLs
    if not request.base_video_url.startswith(("http://", "https://", "s3://")):
        raise HTTPException(
            status_code=400,
            detail="base_video_url must be HTTP/HTTPS or s3://bucket/key"
        )

    if request.bgm_url and not request.bgm_url.startswith(("http://", "https://", "s3://")):
        raise HTTPException(
            status_code=400,
            detail="bgm_url must be HTTP/HTTPS or s3://bucket/key"
        )

    # Create job
    job = RenderJob(
        job_id=job_id,
        script=request.script,
        base_video_url=request.base_video_url,
        bgm_url=request.bgm_url,
        subtitle_style=request.settings.subtitle_style if request.settings else None,
        resolution=request.settings.resolution if request.settings else None,
        title=request.title
    )

    # Add to queue
    job_manager = get_job_manager()
    job_manager.add_job(job)

    logger.info(f"Job {job_id} queued (queue size: {job_manager.get_queue_size()})")

    # Return immediately with queued status
    return RenderResponse(
        job_id=job_id,
        status="queued"
    )


@router.post("/voiceover", response_model=VoiceoverResponse)
async def generate_voiceover(request: VoiceoverRequest):
    """
    Generate a voiceover from a script and upload to S3.
    This endpoint processes synchronously and returns the S3 location.
    """
    job_id = str(uuid.uuid4())
    job_dir = None
    job_succeeded = False

    # Validate script length
    if len(request.script) > MAX_SCRIPT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Script too long. Maximum {MAX_SCRIPT_LENGTH} characters allowed."
        )

    if len(request.script.strip()) == 0:
        raise HTTPException(status_code=400, detail="Script cannot be empty")

    try:
        job_dir = Path(tempfile.gettempdir()) / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        sentences = script_processor.process(request.script)
        if not sentences:
            raise ValueError("Script processing resulted in no sentences")
        if len(sentences) > MAX_SENTENCE_COUNT:
            raise ValueError(
                f"Script produces too many sentences. Maximum {MAX_SENTENCE_COUNT} allowed."
            )

        voice_file = await tts_service.generate_voiceover(sentences, job_dir)
        voice_url = await s3_uploader.upload_voice(voice_file, job_id)

        job_succeeded = True
        return VoiceoverResponse(
            job_id=job_id,
            status="completed",
            voice_url=voice_url
        )

    except Exception as e:
        logger.exception(f"[{job_id}] Voiceover generation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if job_dir:
            if job_succeeded and CLEANUP_ON_SUCCESS:
                file_manager.cleanup_job_directory(job_dir)
            elif not job_succeeded and CLEANUP_ON_FAILURE:
                file_manager.cleanup_job_directory(job_dir)


@router.post("/render-video", response_model=RenderResponse)
async def render_video_manual(request: ManualRenderRequest):
    """
    Render a video using a provided voiceover.

    This endpoint processes synchronously and returns S3 locations.
    """
    job_id = str(uuid.uuid4())
    job_dir = None
    job_succeeded = False

    # Validate script length
    if len(request.script) > MAX_SCRIPT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Script too long. Maximum {MAX_SCRIPT_LENGTH} characters allowed."
        )

    if len(request.script.strip()) == 0:
        raise HTTPException(status_code=400, detail="Script cannot be empty")

    _validate_s3_field(request.voiceover_url, "voiceover_url")
    _validate_s3_field(request.base_video_url, "base_video_url")
    if request.bgm_url:
        _validate_s3_field(request.bgm_url, "bgm_url")

    try:
        job_dir = Path(tempfile.gettempdir()) / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        sentences = script_processor.process(request.script)
        if not sentences:
            raise ValueError("Script processing resulted in no sentences")
        if len(sentences) > MAX_SENTENCE_COUNT:
            raise ValueError(
                f"Script produces too many sentences. Maximum {MAX_SENTENCE_COUNT} allowed."
            )

        # Generate per-sentence audio for subtitle timing
        await tts_service.generate_voiceover(sentences, job_dir)

        # Download base video and get dimensions (for correct subtitle alignment/scaling)
        base_video_path = await video_renderer.download_video(request.base_video_url, job_dir)
        video_dimensions = await video_renderer.get_video_dimensions(base_video_path)
        
        if video_dimensions is None:
            logger.warning(f"[{job_id}] Failed to get video dimensions, using default 1920x1080 for subtitles")

        subtitle_file = await subtitle_service.generate_subtitles(
            sentences,
            job_dir,
            request.settings.subtitle_style if request.settings else None,
            video_dimensions
        )

        voice_input = await _download_audio_file(
            request.voiceover_url,
            job_dir,
            filename_stem="voice_input"
        )
        voice_file = _ensure_wav(voice_input, job_dir / "voice.wav")

        voice_url = request.voiceover_url

        mixed_audio = await audio_mixer.mix_audio(
            voice_file,
            job_dir,
            request.bgm_url
        )

        final_video = await video_renderer.render_video(
            base_video_path,
            mixed_audio,
            subtitle_file,
            job_dir,
            request.settings.resolution if request.settings else None
        )

        # Determine aspect ratio from video dimensions
        aspect_ratio = "16:9"  # default
        if video_dimensions:
            width, height = video_dimensions
            ratio = width / height
            if abs(ratio - 16/9) < 0.1:
                aspect_ratio = "16:9"
            elif abs(ratio - 9/16) < 0.1:
                aspect_ratio = "9:16"
            elif abs(ratio - 1.0) < 0.1:
                aspect_ratio = "1:1"

        # Generate thumbnail first (needed for both regular videos and Shorts)
        thumbnail_url = None
        thumbnail_file = None
        try:
            thumbnail_file = await thumbnail_service.generate_thumbnail(
                video_file=final_video,
                job_dir=job_dir,
                script=request.script,
                aspect_ratio=aspect_ratio
            )
        except Exception as e:
            logger.warning(f"Thumbnail generation failed for job {job_id}: {str(e)}")

        # For Shorts: bake thumbnail into video as first frame (YouTube API limitation)
        # YouTube doesn't allow setting thumbnails via API for Shorts
        if request.is_short and thumbnail_file:
            try:
                logger.info(f"[{job_id}] Baking thumbnail into Short video")
                final_video = await video_renderer.bake_thumbnail_into_video(
                    final_video,
                    thumbnail_file,
                    job_dir
                )
                logger.info(f"[{job_id}] Thumbnail baked successfully")
            except Exception as e:
                logger.warning(f"[{job_id}] Thumbnail baking failed (continuing without): {str(e)}")

        # Also support external thumbnail URL for Shorts (e.g., AI-generated)
        if request.is_short and request.thumbnail_url and not thumbnail_file:
            try:
                logger.info(f"[{job_id}] Baking external thumbnail into Short video")
                thumbnail_download = await _download_thumbnail_file(
                    request.thumbnail_url,
                    job_dir
                )
                final_video = await video_renderer.bake_thumbnail_into_video(
                    final_video,
                    thumbnail_download,
                    job_dir
                )
                logger.info(f"[{job_id}] External thumbnail baked successfully")
            except Exception as e:
                logger.warning(f"[{job_id}] External thumbnail baking failed: {str(e)}")

        subtitles_url = await s3_uploader.upload_subtitle(subtitle_file, job_id)
        video_url = await s3_uploader.upload_video(final_video, job_id)

        # Upload thumbnail to S3
        if thumbnail_file:
            try:
                thumbnail_url = await s3_uploader.upload_thumbnail(thumbnail_file, job_id)
            except Exception as e:
                logger.warning(f"Thumbnail upload failed for job {job_id}: {str(e)}")

        job_succeeded = True
        return RenderResponse(
            job_id=job_id,
            status="completed",
            voice_url=voice_url,
            subtitles_url=subtitles_url,
            video_url=video_url,
            thumbnail_url=thumbnail_url
        )

    except Exception as e:
        logger.exception(f"[{job_id}] Manual render failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if job_dir:
            if job_succeeded and CLEANUP_ON_SUCCESS:
                file_manager.cleanup_job_directory(job_dir)
            elif not job_succeeded and CLEANUP_ON_FAILURE:
                file_manager.cleanup_job_directory(job_dir)


@router.get("/status/{job_id}", response_model=RenderResponse)
async def get_job_status(job_id: str):
    """
    Get the status of a render job.

    Returns job status and S3 locations when available.
    """
    job_manager = get_job_manager()
    job_status = job_manager.get_job_status(job_id)

    if not job_status:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return RenderResponse(
        job_id=job_status.job_id,
        status=job_status.status,
        voice_url=job_status.voice_url,
        subtitles_url=job_status.subtitles_url,
        video_url=job_status.video_url,
        thumbnail_url=job_status.thumbnail_url,
        error=job_status.error
    )


@router.get("/")
async def root():
    """Root endpoint"""
    job_manager = get_job_manager()
    return {
        "service": "Video Rendering Service",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "render": "POST /render",
            "voiceover": "POST /voiceover",
            "render_video": "POST /render-video",
            "status": "GET /status/{job_id}"
        },
        "queue_info": {
            "queue_size": job_manager.get_queue_size(),
            "active_jobs": job_manager.get_active_jobs_count(),
            "max_concurrent": job_manager.max_concurrent_jobs
        }
    }
