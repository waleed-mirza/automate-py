import subprocess
import logging
from pathlib import Path
import httpx

from src.utils.constants import (
    AUDIO_BITRATE,
    DOWNLOAD_TIMEOUT_SECONDS,
    FFMPEG_PRESET,
    MAX_VIDEO_SIZE_MB,
)
from src.utils.s3_uploader import s3_uploader

logger = logging.getLogger(__name__)


class VideoRenderer:
    """
    Renders final video with burned subtitles and voiceover audio.
    """

    def __init__(self):
        self.ffmpeg_preset = FFMPEG_PRESET  # Balance between speed and quality

    async def render_video(
        self,
        base_video_url: str,
        audio_file: Path,
        subtitle_file: Path,
        job_dir: Path,
        resolution: str = None
    ) -> Path:
        """
        Render final video with subtitles and audio.

        Args:
            base_video_url: URL to base video file
            audio_file: Path to audio file (voice or mixed)
            subtitle_file: Path to ASS subtitle file
            job_dir: Job directory for temporary files
            resolution: Optional resolution (e.g., "1920x1080")

        Returns:
            Path to final.mp4 file
        """
        logger.info(f"Rendering video (job: {job_dir.name})")

        # Download base video
        base_video = await self._download_video(base_video_url, job_dir)

        # Render final video
        final_video = job_dir / "final.mp4"
        await self._render_with_ffmpeg(
            base_video,
            audio_file,
            subtitle_file,
            final_video,
            resolution
        )

        logger.info(f"Video rendering complete: {final_video}")
        return final_video

    async def _download_video(self, url: str, job_dir: Path) -> Path:
        """
        Download base video from URL.

        Args:
            url: URL to video file
            job_dir: Job directory for saving file

        Returns:
            Path to downloaded video file
        """
        resolved_url = url
        if s3_uploader.is_s3_location(url):
            resolved_url = s3_uploader.get_presigned_url(url)

        logger.info(f"Downloading base video: {resolved_url}")

        try:
            # Determine file extension from URL or default to mp4
            extension = ".mp4"
            if "." in url.split("/")[-1]:
                ext = url.split(".")[-1].split("?")[0]
                if ext.lower() in ["mp4", "mov", "avi", "mkv"]:
                    extension = "." + ext

            base_video = job_dir / f"base{extension}"
            max_bytes = MAX_VIDEO_SIZE_MB * 1024 * 1024
            downloaded_bytes = 0

            # Stream download for large files
            async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS) as client:
                async with client.stream("GET", resolved_url) as response:
                    response.raise_for_status()

                    with open(base_video, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            downloaded_bytes += len(chunk)
                            if downloaded_bytes > max_bytes:
                                raise RuntimeError(
                                    f"Base video exceeds limit of {MAX_VIDEO_SIZE_MB} MB"
                                )
                            f.write(chunk)

            logger.debug(f"Downloaded base video: {base_video}")
            return base_video

        except httpx.HTTPError as e:
            raise RuntimeError(f"Failed to download base video: {str(e)}")
        except Exception as e:
            raise RuntimeError(f"Error downloading video: {str(e)}")

    async def _render_with_ffmpeg(
        self,
        video_file: Path,
        audio_file: Path,
        subtitle_file: Path,
        output_file: Path,
        resolution: str = None
    ):
        """
        Render final video with FFmpeg.

        Args:
            video_file: Path to base video
            audio_file: Path to audio file
            subtitle_file: Path to subtitle file
            output_file: Path to output video
            resolution: Optional resolution string (e.g., "1920x1080")
        """
        try:
            # Build video filter
            # Burn subtitles into video
            video_filter = f"ass={subtitle_file}"

            # Add scaling if resolution specified
            if resolution:
                video_filter = f"{video_filter},scale={resolution}"

            cmd = [
                "ffmpeg",
                "-i", str(video_file),
                "-i", str(audio_file),
                "-vf", video_filter,
                "-map", "0:v",  # Video from first input
                "-map", "1:a",  # Audio from second input
                "-c:v", "libx264",
                "-preset", self.ffmpeg_preset,
                "-c:a", "aac",
                "-b:a", AUDIO_BITRATE,
                "-shortest",  # End when shortest stream ends
                str(output_file),
                "-y"  # Overwrite output file
            ]

            logger.debug(f"Running FFmpeg command: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            logger.debug("Video rendering successful")

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"FFmpeg video rendering failed: {e.stderr}")
        except Exception as e:
            raise RuntimeError(f"Failed to render video: {str(e)}")


# Singleton instance
video_renderer = VideoRenderer()
