import httpx
import logging
import asyncio
from datetime import datetime
from typing import Literal, Optional

from config import settings
from src.utils.constants import WEBHOOK_RETRY_ATTEMPTS, WEBHOOK_TIMEOUT

logger = logging.getLogger(__name__)


class WebhookService:
    """
    Sends webhook notifications for pipeline events.
    Logs failures but doesn't block processing.
    """

    def __init__(self):
        self.webhook_url = settings.webhook_url
        self.timeout = WEBHOOK_TIMEOUT

    async def send_voiceover_uploaded(self, job_id: str, voice_url: str):
        """
        Send webhook notification when voiceover is uploaded.

        Args:
            job_id: Job ID
            voice_url: S3 location for uploaded voice.wav
        """
        payload = {
            "event": "voiceover_uploaded",
            "job_id": job_id,
            "voice_url": voice_url,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

        await self._send_webhook("voiceover_uploaded", payload)

    async def send_video_completed(
        self,
        job_id: str,
        voice_url: str,
        subtitles_url: str,
        video_url: str,
        thumbnail_url: Optional[str] = None
    ):
        """
        Send webhook notification when video rendering is complete.

        Args:
            job_id: Job ID
            voice_url: S3 location for uploaded voice.wav
            subtitles_url: S3 location for uploaded subs.ass
            video_url: S3 location for uploaded final.mp4
            thumbnail_url: Optional S3 location for thumbnail image
        """
        payload = {
            "event": "video_completed",
            "job_id": job_id,
            "voice_url": voice_url,
            "subtitles_url": subtitles_url,
            "video_url": video_url,
            "thumbnail_url": thumbnail_url,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

        await self._send_webhook("video_completed", payload)

    async def send_job_failed(
        self,
        job_id: str,
        error: str,
        step: Optional[str] = None,
        error_type: Optional[str] = None
    ):
        """
        Send webhook notification when a job fails.

        Args:
            job_id: Job ID
            error: Error message
            step: Current step when failure occurred
            error_type: Type of error (e.g., "validation", "processing", "upload")
        """
        payload = {
            "event": "job_failed",
            "job_id": job_id,
            "error": error,
            "step": step,
            "error_type": error_type or "processing",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

        await self._send_webhook("job_failed", payload)

    async def send_status_update(
        self,
        job_id: str,
        status: str,
        step: Optional[str] = None,
        progress: Optional[int] = None,
        message: Optional[str] = None
    ):
        """
        Send webhook notification for status updates.

        Args:
            job_id: Job ID
            status: Current status (queued, processing, completed, failed)
            step: Current processing step
            progress: Progress percentage (0-100)
            message: Optional status message
        """
        payload = {
            "event": "status_update",
            "job_id": job_id,
            "status": status,
            "step": step,
            "progress": progress,
            "message": message,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

        await self._send_webhook("status_update", payload)

    async def _send_webhook(self, event: str, payload: dict):
        """
        Send webhook notification.
        Logs failures but doesn't raise exceptions.

        Args:
            event: Event type
            payload: JSON payload to send
        """
        if not self.webhook_url:
            logger.warning(f"Webhook URL not configured, skipping {event} notification")
            return

        max_attempts = max(1, WEBHOOK_RETRY_ATTEMPTS + 1)
        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            try:
                logger.info(f"Sending webhook: {event} to {self.webhook_url} (attempt {attempt}/{max_attempts})")

                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        self.webhook_url,
                        json=payload,
                        headers={"Content-Type": "application/json"}
                    )

                    # Log response
                    if 200 <= response.status_code < 300:
                        logger.info(
                            f"Webhook {event} sent successfully (status: {response.status_code})"
                        )
                        return

                    logger.warning(
                        f"Webhook {event} returned non-success status: {response.status_code} - {response.text}"
                    )

            except httpx.TimeoutException:
                logger.warning(f"Webhook {event} timed out after {self.timeout}s")
            except httpx.HTTPError as e:
                logger.warning(f"Webhook {event} HTTP error: {str(e)}")
            except Exception as e:
                logger.warning(f"Webhook {event} failed: {str(e)}")

            if attempt < max_attempts:
                await asyncio.sleep(0.5 * attempt)


# Singleton instance
webhook_service = WebhookService()
