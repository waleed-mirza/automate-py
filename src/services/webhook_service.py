import httpx
import logging
import asyncio
from datetime import datetime
from typing import Literal

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
        video_url: str
    ):
        """
        Send webhook notification when video rendering is complete.

        Args:
            job_id: Job ID
            voice_url: S3 location for uploaded voice.wav
            subtitles_url: S3 location for uploaded subs.ass
            video_url: S3 location for uploaded final.mp4
        """
        payload = {
            "event": "video_completed",
            "job_id": job_id,
            "voice_url": voice_url,
            "subtitles_url": subtitles_url,
            "video_url": video_url,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

        await self._send_webhook("video_completed", payload)

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
