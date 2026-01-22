import subprocess
import logging
import json
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
        video_source: str | Path,
        audio_file: Path,
        subtitle_file: Path,
        job_dir: Path,
        resolution: str | None = None
    ) -> Path:
        """
        Render final video with subtitles and audio.

        Args:
            video_source: URL to base video file OR Path to local video file
            audio_file: Path to audio file (voice or mixed)
            subtitle_file: Path to ASS subtitle file
            job_dir: Job directory for temporary files
            resolution: Optional resolution (e.g., "1920x1080")

        Returns:
            Path to final.mp4 file
        """
        logger.info(f"Rendering video (job: {job_dir.name})")

        # Download base video if URL provided, otherwise use local path
        if isinstance(video_source, Path):
            base_video = video_source
        else:
            base_video = await self.download_video(video_source, job_dir)

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

    async def get_video_dimensions(self, video_path: Path) -> tuple[int, int] | None:
        """
        Get video width and height using ffprobe.
        Handles rotation (swaps width/height if 90 or 270 degrees).

        Args:
            video_path: Path to video file

        Returns:
            Tuple of (width, height) or None if detection fails
        """
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,rotation:stream_tags=rotate",
                "-of", "json",
                str(video_path)
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            data = json.loads(result.stdout)
            if not data.get("streams"):
                logger.warning(f"No video streams found in {video_path.name}")
                return None
                
            stream = data["streams"][0]
            width = int(stream.get("width", 0))
            height = int(stream.get("height", 0))

            # Check for rotation
            rotation = 0
            # Try direct rotation field (some ffmpeg versions)
            if "rotation" in stream:
                rotation = int(float(stream["rotation"]))
            # Try tags
            elif "tags" in stream and "rotate" in stream["tags"]:
                rotation = int(float(stream["tags"]["rotate"]))
            # Try side_data_list (common in newer ffmpeg for phone videos)
            elif "side_data_list" in stream:
                for side_data in stream["side_data_list"]:
                    if "rotation" in side_data:
                        rotation = int(float(side_data["rotation"]))
                        break
            
            # Normalize rotation to 0-360
            rotation = rotation % 360
            
            # Swap dimensions if rotated 90 or 270 degrees
            if rotation in [90, 270]:
                width, height = height, width
                logger.info(f"Video detected with {rotation} deg rotation. Swapping dimensions to {width}x{height}")

            if width == 0 or height == 0:
                logger.warning(f"Invalid video dimensions detected: {width}x{height}")
                return None

            logger.debug(f"Video dimensions for {video_path.name}: {width}x{height}")
            return width, height

        except subprocess.CalledProcessError as e:
            logger.warning(f"ffprobe failed to get dimensions: {e.stderr}")
            return None
        except Exception as e:
            logger.warning(f"Failed to get video dimensions: {str(e)}")
            return None

    async def download_video(self, url: str, job_dir: Path) -> Path:
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
        resolution: str | None = None
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
