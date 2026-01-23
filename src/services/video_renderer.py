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

    async def bake_thumbnail_into_video(
        self,
        video_file: Path,
        thumbnail_file: Path,
        job_dir: Path,
    ) -> Path:
        """
        Prepend thumbnail as a short frame at the start of the video.
        This is the workaround for YouTube Shorts not supporting custom thumbnails via API.
        YouTube will auto-select this frame as the thumbnail.

        Args:
            video_file: Path to the rendered video
            thumbnail_file: Path to the thumbnail image
            job_dir: Job directory for temporary files

        Returns:
            Path to the modified video with baked thumbnail
        """
        logger.info(f"Baking thumbnail into video for Shorts")

        output_file = job_dir / "final_with_thumb.mp4"

        try:
            # Get video stream parameters (dimensions, fps, pixel format)
            video_probe_cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate,pix_fmt,sample_aspect_ratio",
                "-of", "json",
                str(video_file)
            ]
            video_probe_result = subprocess.run(video_probe_cmd, capture_output=True, text=True, check=True)
            video_probe_data = json.loads(video_probe_result.stdout)
            video_stream = video_probe_data["streams"][0]
            
            width = video_stream["width"]
            height = video_stream["height"]
            fps_val = video_stream["r_frame_rate"]
            pix_fmt = video_stream.get("pix_fmt", "yuv420p")

            # Get audio stream parameters (sample rate, channels)
            audio_probe_cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=sample_rate,channels",
                "-of", "json",
                str(video_file)
            ]
            audio_probe_result = subprocess.run(audio_probe_cmd, capture_output=True, text=True, check=True)
            audio_probe_data = json.loads(audio_probe_result.stdout)
            
            sample_rate = 44100
            channels = "stereo"
            if audio_probe_data.get("streams"):
                audio_stream = audio_probe_data["streams"][0]
                sample_rate = int(audio_stream.get("sample_rate", 44100))
                num_channels = int(audio_stream.get("channels", 2))
                channels = "stereo" if num_channels >= 2 else "mono"

            # Single-pass concat filter approach
            # 1. Scale/pad thumbnail to match video dimensions and FPS
            # 2. Generate silent audio for thumbnail segment
            # 3. Setsar on main video to match
            # 4. Concat everything
            filter_complex = (
                f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps_val}[v0];"
                f"anullsrc=r={sample_rate}:cl={channels}:d=0.5[a0];"
                f"[1:v]setsar=1[v1];"
                f"[v0][a0][v1][1:a]concat=n=2:v=1:a=1[v][a]"
            )

            cmd = [
                "ffmpeg",
                "-loop", "1",
                "-t", "0.5",
                "-i", str(thumbnail_file),
                "-i", str(video_file),
                "-filter_complex", filter_complex,
                "-map", "[v]",
                "-map", "[a]",
                "-c:v", "libx264",
                "-preset", self.ffmpeg_preset,
                "-pix_fmt", pix_fmt,
                "-c:a", "aac",
                "-b:a", AUDIO_BITRATE,
                "-movflags", "+faststart",
                str(output_file),
                "-y"
            ]
            
            logger.debug(f"Baking thumbnail with command: {' '.join(cmd)}")
            subprocess.run(cmd, capture_output=True, text=True, check=True)

            logger.info(f"Thumbnail baked into video: {output_file}")
            return output_file

        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg thumbnail baking failed: {e.stderr}")
            raise RuntimeError(f"FFmpeg thumbnail baking failed: {e.stderr}")
        except Exception as e:
            logger.error(f"Failed to bake thumbnail into video: {str(e)}")
            raise RuntimeError(f"Failed to bake thumbnail into video: {str(e)}")


# Singleton instance
video_renderer = VideoRenderer()
