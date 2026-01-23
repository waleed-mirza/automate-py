import logging
import subprocess
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)


class FrameThumbnailService:
    """
    Generates a thumbnail image from a rendered video using FFmpeg.
    Used when thumbnail_provider = "frame"
    """

    async def generate_thumbnail(
        self,
        video_file: Path,
        job_dir: Path,
        script: str | None = None,
        aspect_ratio: str = "16:9",
        title: str | None = None
    ) -> Path:
        """
        Extract a single frame from the video to create a thumbnail.
        Note: script, aspect_ratio, and title are ignored for frame extraction.
        """
        if not video_file.exists():
            raise FileNotFoundError(f"Video file not found: {video_file}")

        duration = self._get_video_duration(video_file)
        if duration <= 0:
            raise RuntimeError("Invalid video duration returned by ffprobe")

        timestamp = max(1.0, duration * 0.1)
        if timestamp > duration:
            timestamp = max(duration * 0.5, 0.0)

        thumbnail_file = job_dir / "thumbnail.jpg"
        self._extract_frame(video_file, thumbnail_file, timestamp)

        if not thumbnail_file.exists():
            raise RuntimeError("Thumbnail generation failed: output file missing")

        return thumbnail_file

    def _get_video_duration(self, video_file: Path) -> float:
        """Get duration of video file using ffprobe."""
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_file)
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            duration_str = result.stdout.strip()
            duration = float(duration_str)
            logger.debug(f"Video duration for {video_file.name}: {duration:.2f}s")
            return duration

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ffprobe failed: {e.stderr}")
        except Exception as e:
            raise RuntimeError(f"Failed to get video duration: {str(e)}")

    def _extract_frame(self, video_file: Path, output_file: Path, timestamp: float):
        """Extract a single frame from a video at a specific timestamp."""
        try:
            cmd = [
                "ffmpeg",
                "-ss", f"{timestamp:.3f}",
                "-i", str(video_file),
                "-vframes", "1",
                "-q:v", "2",
                str(output_file),
                "-y"
            ]

            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            logger.debug(f"Thumbnail extracted at {timestamp:.2f}s: {output_file}")

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"FFmpeg thumbnail extraction failed: {e.stderr}")
        except Exception as e:
            raise RuntimeError(f"Failed to extract thumbnail: {str(e)}")


class ThumbnailService:
    """
    Router for thumbnail generation.
    Selects provider based on settings.thumbnail_provider:
    - "frame": FFmpeg frame extraction (default)
    - "cloudflare": Cloudflare Workers AI generation + Pillow text overlay
    """

    def __init__(self):
        self._frame_service = FrameThumbnailService()
        self._ai_service = None  # Lazy load to avoid import errors if not configured

    async def generate_thumbnail(
        self,
        video_file: Path,
        job_dir: Path,
        script: str | None = None,
        aspect_ratio: str = "16:9",
        title: str | None = None
    ) -> Path:
        """
        Generate thumbnail using configured provider.
        
        Args:
            video_file: Path to rendered video file (required for frame extraction)
            job_dir: Job directory for temporary files
            script: Video script (used by AI provider for context)
            aspect_ratio: Video aspect ratio (16:9, 9:16, 1:1)
            title: Optional title override for text overlay
            
        Returns:
            Path to thumbnail.jpg
        """
        provider = settings.thumbnail_provider.lower()
        
        if provider == "cloudflare":
            if not script:
                logger.warning("AI thumbnail requested but no script provided, falling back to frame extraction")
                provider = "frame"
            elif not settings.cloudflare_account_id or not settings.cloudflare_api_token:
                logger.warning("Cloudflare credentials not configured, falling back to frame extraction")
                provider = "frame"
        
        if provider == "cloudflare":
            logger.info("Generating AI thumbnail via Cloudflare Workers AI")
            assert script is not None  # Guaranteed by checks above
            try:
                return await self._get_ai_service().generate_thumbnail(
                    script=script,
                    job_dir=job_dir,
                    aspect_ratio=aspect_ratio,
                    title=title
                )
            except Exception as e:
                logger.warning(f"Cloudflare AI thumbnail failed, falling back to frame extraction: {e}")
                return await self._frame_service.generate_thumbnail(
                    video_file=video_file,
                    job_dir=job_dir,
                    script=script,
                    aspect_ratio=aspect_ratio,
                    title=title
                )
        else:
            logger.info(f"Generating thumbnail via frame extraction")
            return await self._frame_service.generate_thumbnail(
                video_file=video_file,
                job_dir=job_dir,
                script=script,
                aspect_ratio=aspect_ratio,
                title=title
            )

    def _get_ai_service(self):
        """Lazy load AI thumbnail service."""
        if self._ai_service is None:
            from src.services.ai_thumbnail_service import ai_thumbnail_service
            self._ai_service = ai_thumbnail_service
        return self._ai_service


# Singleton instance
thumbnail_service = ThumbnailService()
