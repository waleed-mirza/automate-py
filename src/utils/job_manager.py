import asyncio
from typing import Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class JobStatus:
    """Job status information"""
    job_id: str
    status: str  # queued, processing, completed, failed
    voice_url: Optional[str] = None
    subtitles_url: Optional[str] = None
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class RenderJob:
    """Job data for rendering pipeline"""
    job_id: str
    script: str
    base_video_url: str
    bgm_url: Optional[str] = None
    subtitle_style: Optional[dict] = None
    resolution: Optional[str] = None
    title: Optional[str] = None
    desired_duration: Optional[float] = None


class JobManager:
    """
    Manages job queue and status tracking.
    Limits concurrent jobs using asyncio.Semaphore.
    """

    def __init__(self, max_concurrent_jobs: int = 3):
        self.max_concurrent_jobs = max_concurrent_jobs
        self.semaphore = asyncio.Semaphore(max_concurrent_jobs)
        self.job_queue: asyncio.Queue[RenderJob] = asyncio.Queue()
        self.job_statuses: Dict[str, JobStatus] = {}
        self._workers_started = False

    def add_job(self, job: RenderJob):
        """
        Add a job to the queue.

        Args:
            job: RenderJob to process
        """
        # Create job status
        self.job_statuses[job.job_id] = JobStatus(
            job_id=job.job_id,
            status="queued"
        )

        # Add to queue
        self.job_queue.put_nowait(job)
        logger.info(f"Job {job.job_id} added to queue (queue size: {self.job_queue.qsize()})")

    def get_job_status(self, job_id: str) -> Optional[JobStatus]:
        """
        Get status of a job.

        Args:
            job_id: Job ID

        Returns:
            JobStatus if found, None otherwise
        """
        return self.job_statuses.get(job_id)

    def update_job_status(
        self,
        job_id: str,
        status: str,
        voice_url: Optional[str] = None,
        subtitles_url: Optional[str] = None,
        video_url: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        error: Optional[str] = None
    ):
        """
        Update job status.

        Args:
            job_id: Job ID
            status: New status
            voice_url: Optional S3 location for voice audio
            subtitles_url: Optional S3 location for subtitles
            video_url: Optional S3 location for rendered video
            thumbnail_url: Optional S3 location for thumbnail image
            error: Optional error message
        """
        if job_id not in self.job_statuses:
            logger.warning(f"Attempted to update non-existent job: {job_id}")
            return

        job_status = self.job_statuses[job_id]
        job_status.status = status
        job_status.updated_at = datetime.utcnow()

        if voice_url is not None:
            job_status.voice_url = voice_url
        if subtitles_url is not None:
            job_status.subtitles_url = subtitles_url
        if video_url is not None:
            job_status.video_url = video_url
        if thumbnail_url is not None:
            job_status.thumbnail_url = thumbnail_url
        if error is not None:
            job_status.error = error

        logger.info(f"Job {job_id} status updated to: {status}")

    async def get_next_job(self) -> RenderJob:
        """
        Get next job from queue (blocks if empty).

        Returns:
            Next RenderJob to process
        """
        return await self.job_queue.get()

    def get_queue_size(self) -> int:
        """Get current queue size"""
        return self.job_queue.qsize()

    def get_active_jobs_count(self) -> int:
        """Get number of jobs currently processing"""
        return sum(1 for job in self.job_statuses.values() if job.status == "processing")


# Global singleton instance
job_manager: Optional[JobManager] = None


def get_job_manager() -> JobManager:
    """Get the global job manager instance"""
    global job_manager
    if job_manager is None:
        from config import settings
        job_manager = JobManager(max_concurrent_jobs=settings.max_concurrent_jobs)
    return job_manager
