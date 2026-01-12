import httpx
import logging
from datetime import datetime
from typing import Literal

from config import settings

logger = logging.getLogger(__name__)


class WebhookService:
    """
    Sends webhook notifications for pipeline events.
    Logs failures but doesn't block processing.
    """

    def __init__(self):
        self.webhook_url = settings.webhook_url
        self.timeout = 5.0  # 5 second timeout

    async def send_voiceover_uploaded(self, job_id: str, voice_url: str):
        """
        Send webhook notification when voiceover is uploaded.

        Args:
            job_id: Job ID
            voice_url: URL to uploaded voice.wav
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
            voice_url: URL to uploaded voice.wav
            subtitles_url: URL to uploaded subs.ass
            video_url: URL to uploaded final.mp4
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

        try:
            logger.info(f"Sending webhook: {event} to {self.webhook_url}")

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )

                # Log response
                if response.status_code >= 200 and response.status_code < 300:
                    logger.info(f"Webhook {event} sent successfully (status: {response.status_code})")
                else:
                    logger.warning(
                        f"Webhook {event} returned non-success status: {response.status_code} - {response.text}"
                    )

        except httpx.TimeoutException:
            logger.warning(f"Webhook {event} timed out after {self.timeout}s")
        except httpx.HTTPError as e:
            logger.warning(f"Webhook {event} HTTP error: {str(e)}")
        except Exception as e:
            logger.warning(f"Webhook {event} failed: {str(e)}")


# Singleton instance
webhook_service = WebhookService()
