from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import uuid
import logging
from pathlib import Path
import tempfile

from src.utils.job_manager import get_job_manager, RenderJob
from src.utils.constants import MAX_SCRIPT_LENGTH

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
    settings: Optional[RenderSettings] = None


class RenderResponse(BaseModel):
    """Response model for /render endpoint"""
    job_id: str
    status: str
    voice_url: Optional[str] = None
    subtitles_url: Optional[str] = None
    video_url: Optional[str] = None
    error: Optional[str] = None


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
        resolution=request.settings.resolution if request.settings else None
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
            "status": "GET /status/{job_id}"
        },
        "queue_info": {
            "queue_size": job_manager.get_queue_size(),
            "active_jobs": job_manager.get_active_jobs_count(),
            "max_concurrent": job_manager.max_concurrent_jobs
        }
    }
