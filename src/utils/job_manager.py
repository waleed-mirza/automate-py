import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import aiosqlite

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class JobStatus:
    """Job status information"""
    job_id: str
    status: str  # queued, processing, completed, failed
    step: Optional[str] = None
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
    video_mode: str = "base_video"  # "base_video" or "generated_images"
    aspect_ratio: str = "16:9"
    language: Optional[str] = None
    image_paths: Optional[list] = None  # For generated_images mode
    image_durations: Optional[list] = None  # For generated_images mode
    lead_time: Optional[float] = None  # Lead time for generated_images mode


class JobManager:
    """
    Manages job queue and status tracking.
    Limits concurrent jobs using asyncio.Semaphore.
    """

    def __init__(self, max_concurrent_jobs: int = 3, db_path: Optional[str] = None):
        self.max_concurrent_jobs = max_concurrent_jobs
        self.semaphore = asyncio.Semaphore(max_concurrent_jobs)
        self.job_queue: asyncio.Queue[RenderJob] = asyncio.Queue()
        self.job_statuses: Dict[str, JobStatus] = {}
        self._workers_started = False
        self.db_path = Path(db_path or settings.job_db_path)
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path.as_posix())
        db = self._db
        if db is None:
            raise RuntimeError("Failed to initialize job database")

        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                step TEXT,
                payload TEXT NOT NULL,
                voice_url TEXT,
                subtitles_url TEXT,
                video_url TEXT,
                thumbnail_url TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.commit()
        await self._load_existing_jobs()

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None

    async def _load_existing_jobs(self):
        db = self._db
        if db is None:
            return

        async with db.execute(
            "SELECT job_id, status, step, payload, voice_url, subtitles_url, video_url, thumbnail_url, error, created_at, updated_at FROM jobs"
        ) as cursor:
            async for row in cursor:
                (
                    job_id,
                    status,
                    step,
                    payload,
                    voice_url,
                    subtitles_url,
                    video_url,
                    thumbnail_url,
                    error,
                    created_at,
                    updated_at,
                ) = row
                self.job_statuses[job_id] = JobStatus(
                    job_id=job_id,
                    status=status,
                    step=step,
                    voice_url=voice_url,
                    subtitles_url=subtitles_url,
                    video_url=video_url,
                    thumbnail_url=thumbnail_url,
                    error=error,
                    created_at=datetime.fromisoformat(created_at),
                    updated_at=datetime.fromisoformat(updated_at),
                )

        await self._requeue_pending_jobs()

    async def _requeue_pending_jobs(self):
        db = self._db
        if db is None:
            return

        async with db.execute(
            "SELECT job_id, status, payload FROM jobs WHERE status IN ('queued', 'processing')"
        ) as cursor:
            async for row in cursor:
                job_id, status, payload = row
                job_data = json.loads(payload)
                self.job_queue.put_nowait(self._deserialize_job(job_data))
                if status == "processing":
                    await self._update_status_row(job_id, "queued")
                    job_status = self.job_statuses.get(job_id)
                    if job_status:
                        job_status.status = "queued"
                        job_status.updated_at = datetime.utcnow()

        await db.commit()

    async def add_job(self, job: RenderJob):
        """
        Add a job to the queue.

        Args:
            job: RenderJob to process
        """
        # Create job status
        now = datetime.utcnow()
        self.job_statuses[job.job_id] = JobStatus(
            job_id=job.job_id,
            status="queued",
            created_at=now,
            updated_at=now,
        )

        await self._insert_job_row(job)

        # Add to queue
        self.job_queue.put_nowait(job)
        logger.info(f"Job {job.job_id} added to queue (queue size: {self.job_queue.qsize()})")

    async def get_job_status(self, job_id: str) -> Optional[JobStatus]:
        """
        Get status of a job.

        Args:
            job_id: Job ID

        Returns:
            JobStatus if found, None otherwise
        """
        return self.job_statuses.get(job_id)

    async def requeue_job(self, job_id: str) -> JobStatus:
        job_status = self.job_statuses.get(job_id)
        if not job_status:
            raise ValueError(f"Job {job_id} not found")

        if job_status.status == "completed":
            raise ValueError(f"Job {job_id} is already completed")

        if job_status.status in {"queued", "processing"}:
            return job_status

        job = await self._fetch_job_payload(job_id)
        if not job:
            raise ValueError(f"Job {job_id} payload not found")

        self.job_queue.put_nowait(job)
        await self.update_job_status(job_id, "queued")
        return self.job_statuses[job_id]

    async def update_job_status(
        self,
        job_id: str,
        status: str,
        step: Optional[str] = None,
        voice_url: Optional[str] = None,
        subtitles_url: Optional[str] = None,
        video_url: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        error: Optional[str] = None,
        clear_error: bool = False,
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
        if error is not None and not isinstance(error, str):
            error = str(error)
        if error is None and status in {"queued", "processing", "completed"}:
            clear_error = True
        job_status.status = status
        job_status.updated_at = datetime.utcnow()
        if step is not None:
            job_status.step = step

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
        elif clear_error:
            job_status.error = None

        await self._update_status_row(
            job_id,
            status,
            step=step,
            voice_url=voice_url,
            subtitles_url=subtitles_url,
            video_url=video_url,
            thumbnail_url=thumbnail_url,
            error=error,
            clear_error=clear_error,
        )

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

    async def _insert_job_row(self, job: RenderJob):
        if not self._db:
            return

        now = datetime.utcnow().isoformat()
        payload = json.dumps(self._serialize_job(job))
        await self._db.execute(
            """
            INSERT INTO jobs (
                job_id,
                status,
                step,
                payload,
                voice_url,
                subtitles_url,
                video_url,
                thumbnail_url,
                error,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.job_id,
                "queued",
                None,
                payload,
                None,
                None,
                None,
                None,
                None,
                now,
                now,
            ),
        )
        await self._db.commit()

    async def _update_status_row(
        self,
        job_id: str,
        status: str,
        step: Optional[str] = None,
        voice_url: Optional[str] = None,
        subtitles_url: Optional[str] = None,
        video_url: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        error: Optional[str] = None,
        clear_error: bool = False,
    ):
        if not self._db:
            return

        fields = ["status = ?", "updated_at = ?"]
        values = [status, datetime.utcnow().isoformat()]

        if step is not None:
            fields.append("step = ?")
            values.append(step)
        if voice_url is not None:
            fields.append("voice_url = ?")
            values.append(voice_url)
        if subtitles_url is not None:
            fields.append("subtitles_url = ?")
            values.append(subtitles_url)
        if video_url is not None:
            fields.append("video_url = ?")
            values.append(video_url)
        if thumbnail_url is not None:
            fields.append("thumbnail_url = ?")
            values.append(thumbnail_url)
        if error is not None:
            fields.append("error = ?")
            values.append(error)
        elif clear_error:
            fields.append("error = NULL")

        values.append(job_id)

        await self._db.execute(
            f"UPDATE jobs SET {', '.join(fields)} WHERE job_id = ?",
            values,
        )
        await self._db.commit()

    async def update_job_payload(self, job: RenderJob):
        if not self._db:
            return

        payload = json.dumps(self._serialize_job(job))
        await self._db.execute(
            "UPDATE jobs SET payload = ?, updated_at = ? WHERE job_id = ?",
            (payload, datetime.utcnow().isoformat(), job.job_id),
        )
        await self._db.commit()

    async def _fetch_job_payload(self, job_id: str) -> Optional[RenderJob]:
        if not self._db:
            return None

        async with self._db.execute(
            "SELECT payload FROM jobs WHERE job_id = ?",
            (job_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            payload = json.loads(row[0])
            return self._deserialize_job(payload)

    @staticmethod
    def _serialize_job(job: RenderJob) -> dict:
        data = dict(job.__dict__)
        image_paths = data.get("image_paths")
        if image_paths:
            data["image_paths"] = [str(path) for path in image_paths]
        return data

    @staticmethod
    def _deserialize_job(payload: dict) -> RenderJob:
        image_paths = payload.get("image_paths")
        if image_paths:
            payload["image_paths"] = [Path(path) for path in image_paths]
        return RenderJob(**payload)


# Global singleton instance
job_manager: Optional[JobManager] = None


def get_job_manager() -> JobManager:
    """Get the global job manager instance"""
    global job_manager
    if job_manager is None:
        job_manager = JobManager(
            max_concurrent_jobs=settings.max_concurrent_jobs,
            db_path=settings.job_db_path,
        )
    return job_manager
