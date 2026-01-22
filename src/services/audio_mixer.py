import subprocess
import logging
import json
from pathlib import Path
import httpx

from src.utils.constants import DOWNLOAD_TIMEOUT_SECONDS, MAX_AUDIO_SIZE_MB
from src.utils.s3_uploader import s3_uploader

logger = logging.getLogger(__name__)


class AudioMixer:
    """
    Mixes voiceover audio with background music.
    """

    def __init__(self, bgm_volume: float = 0.2, enable_fadeout: bool = True):
        """
        Args:
            bgm_volume: Background music volume (0.0-1.0), default 0.2 (20%)
            enable_fadeout: Whether to fade out BGM near the end
        """
        self.bgm_volume = bgm_volume
        self.enable_fadeout = enable_fadeout
        logger.info(f"AudioMixer initialized with bgm_volume={bgm_volume}, fadeout={enable_fadeout}")

    async def mix_audio(
        self,
        voice_file: Path,
        job_dir: Path,
        bgm_url: str = None
    ) -> Path:
        """
        Mix voiceover with background music.

        Args:
            voice_file: Path to voice.wav file
            job_dir: Job directory for temporary files
            bgm_url: Optional URL to background music file

        Returns:
            Path to mixed audio file (or original voice file if no BGM)
        """
        if not bgm_url:
            logger.info("No background music provided, using voice only")
            return voice_file

        logger.info(f"Mixing audio with background music (job: {job_dir.name})")

        # Download BGM
        bgm_file = await self._download_bgm(bgm_url, job_dir)

        # Mix voice + BGM
        mixed_file = job_dir / "mixed.wav"
        await self._mix_with_ffmpeg(voice_file, bgm_file, mixed_file)

        logger.info(f"Audio mixing complete: {mixed_file}")
        return mixed_file

    async def _download_bgm(self, url: str, job_dir: Path) -> Path:
        """
        Download background music from URL.

        Args:
            url: URL to BGM file
            job_dir: Job directory for saving file

        Returns:
            Path to downloaded BGM file
        """
        resolved_url = url
        if s3_uploader.is_s3_location(url):
            resolved_url = s3_uploader.get_presigned_url(url)

        logger.info(f"Downloading background music: {resolved_url}")

        try:
            # Determine file extension from URL or default to mp3
            extension = ".mp3"
            if "." in url.split("/")[-1]:
                ext = url.split(".")[-1].split("?")[0]
                if ext.lower() in ["mp3", "wav", "aac", "m4a", "flac", "ogg"]:
                    extension = "." + ext

            bgm_file = job_dir / f"bgm{extension}"
            max_bytes = MAX_AUDIO_SIZE_MB * 1024 * 1024
            downloaded_bytes = 0

            async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS) as client:
                async with client.stream("GET", resolved_url) as response:
                    response.raise_for_status()

                    with open(bgm_file, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            downloaded_bytes += len(chunk)
                            if downloaded_bytes > max_bytes:
                                raise RuntimeError(
                                    f"Background music exceeds limit of {MAX_AUDIO_SIZE_MB} MB"
                                )
                            f.write(chunk)

            logger.debug(f"Downloaded BGM: {bgm_file}")
            return bgm_file

        except httpx.HTTPError as e:
            raise RuntimeError(f"Failed to download background music: {str(e)}")
        except Exception as e:
            raise RuntimeError(f"Error downloading BGM: {str(e)}")

    async def _mix_with_ffmpeg(
        self,
        voice_file: Path,
        bgm_file: Path,
        output_file: Path
    ):
        """
        Mix voice and BGM using FFmpeg.

        Args:
            voice_file: Path to voice audio
            bgm_file: Path to background music
            output_file: Path to output mixed audio
        """
        try:
            # Build FFmpeg filter
            # Lower BGM volume and mix with voice
            # Duration is based on voice (first input)
            # normalize=0 prevents amix from reducing volumes (default normalization halves each input)
            filter_complex = f"[1:a]volume={self.bgm_volume}[bgm];[0:a][bgm]amix=inputs=2:duration=first:normalize=0"

            # Add fade-out to BGM if enabled (fade last 2 seconds)
            if self.enable_fadeout:
                # Get voice duration to calculate fade start time
                duration = self._get_audio_duration(voice_file)
                fade_start = max(0, duration - 2)
                logger.debug(f"Voice duration: {duration}s, fade starts at: {fade_start}s")
                filter_complex = f"[1:a]volume={self.bgm_volume},afade=t=out:st={fade_start}:d=2[bgm];[0:a][bgm]amix=inputs=2:duration=first:normalize=0"

            cmd = [
                "ffmpeg",
                "-i", str(voice_file),
                "-i", str(bgm_file),
                "-filter_complex", filter_complex,
                "-c:a", "pcm_s16le",  # WAV format
                str(output_file),
                "-y"  # Overwrite output file
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            logger.debug("Audio mixing successful")

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"FFmpeg audio mixing failed: {e.stderr}")
        except Exception as e:
            raise RuntimeError(f"Failed to mix audio: {str(e)}")

    def _get_audio_duration(self, audio_file: Path) -> float:
        """Get audio duration in seconds using ffprobe."""
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(audio_file)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])


# Singleton instance
audio_mixer = AudioMixer()
