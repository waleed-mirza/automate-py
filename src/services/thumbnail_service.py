import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class ThumbnailService:
    """
    Generates a thumbnail image from a rendered video using FFmpeg.
    """

    async def generate_thumbnail(self, video_file: Path, job_dir: Path) -> Path:
        """
        Extract a single frame from the video to create a thumbnail.

        Args:
            video_file: Path to rendered video file
            job_dir: Job directory for temporary files

        Returns:
            Path to thumbnail.jpg
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
        """
        Get duration of video file using ffprobe.

        Args:
            video_file: Path to video file

        Returns:
            Duration in seconds
        """
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
        """
        Extract a single frame from a video at a specific timestamp.

        Args:
            video_file: Path to video file
            output_file: Path to output thumbnail file
            timestamp: Timestamp in seconds
        """
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


# Singleton instance
thumbnail_service = ThumbnailService()
