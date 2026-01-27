import subprocess
import logging
import json
import asyncio
from pathlib import Path
from typing import Optional
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
        bgm_url: Optional[str] = None,
        target_duration: float | None = None
    ) -> Path:
        """
        Mix voiceover with background music.

        Args:
            voice_file: Path to voice.wav file
            job_dir: Job directory for temporary files
            bgm_url: Optional URL to background music file
            target_duration: Optional forced total duration (forces looping/trimming)

        Returns:
            Path to mixed audio file (or original voice file if no BGM)
        """
        if not bgm_url:
            logger.info("No background music provided, using voice only")
            return voice_file

        logger.info(f"Mixing audio with background music (job: {job_dir.name})")

        # Download BGM
        bgm_file = await self._download_bgm(bgm_url, job_dir)

        # Loop BGM if target duration specified
        if target_duration:
             bgm_file = await self.loop_bgm_to_duration(bgm_file, target_duration, job_dir)

        # Mix voice + BGM
        mixed_file = job_dir / "mixed.wav"
        await self._mix_with_ffmpeg(voice_file, bgm_file, mixed_file, target_duration)

        logger.info(f"Audio mixing complete: {mixed_file}")
        return mixed_file

    async def loop_bgm_to_duration(
        self,
        bgm_file: Path,
        target_duration: float,
        job_dir: Path
    ) -> Path:
        """
        Loop BGM seamlessly to match target duration.
        
        Args:
            bgm_file: Path to BGM audio file
            target_duration: Desired duration in seconds
            job_dir: Job directory for output
            
        Returns:
            Path to looped BGM file
        """
        logger.info(f"Looping BGM to {target_duration}s")
        output_file = job_dir / "looped_bgm.mp3"
        
        try:
            duration = await self._get_audio_duration(bgm_file)
            if duration <= 0:
                return bgm_file
                
            # Calculate loops needed
            loops = int(target_duration / duration) + 2
            
            # Use aloop filter
            # aloop=loop=-1:size=2e+09 (loop infinitely, but we limit by -t)
            # Actually easier: -stream_loop
            
            cmd = [
                "ffmpeg",
                "-stream_loop", "-1",
                "-i", str(bgm_file),
                "-t", str(target_duration),
                # Add fadeout at the end
                "-af", f"afade=t=out:st={target_duration-3}:d=3",
                "-c:a", "libmp3lame",
                "-q:a", "2",
                str(output_file),
                "-y"
            ]
            
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            return output_file
            
        except subprocess.CalledProcessError as e:
            logger.warning(f"FFmpeg BGM loop failed: {e.stderr}")
            return bgm_file
        except Exception as e:
            logger.warning(f"Failed to loop BGM: {str(e)}")
            return bgm_file

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
            if bgm_file.exists() and bgm_file.stat().st_size > 0:
                logger.info(f"Using cached background music: {bgm_file}")
                return bgm_file
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
        output_file: Path,
        target_duration: float | None = None
    ):
        """
        Mix voice and BGM using FFmpeg.

        Args:
            voice_file: Path to voice audio
            bgm_file: Path to background music
            output_file: Path to output mixed audio
            target_duration: Optional forced total duration
        """
        try:
            # Build FFmpeg filter
            
            # 1. Prepare BGM (volume + fadeout)
            bgm_part = f"[1:a]volume={self.bgm_volume}"
            
            # Determine final duration for fadeout calculation
            if target_duration:
                final_duration = target_duration
            else:
                final_duration = await self._get_audio_duration(voice_file)
                
            if self.enable_fadeout:
                fade_start = max(0, final_duration - 2)
                logger.debug(f"Audio duration: {final_duration}s, fade starts at: {fade_start}s")
                bgm_part += f",afade=t=out:st={fade_start}:d=2"
            
            bgm_part += "[bgm]"
            
            # 2. Prepare Voice (padding if needed)
            voice_part = ""
            mix_input_1 = "[0:a]"
            
            if target_duration:
                v_dur = await self._get_audio_duration(voice_file)
                if target_duration > v_dur:
                    # Pad voice with silence to match target duration
                    voice_part = f"[0:a]apad=whole_dur={target_duration}[padded];"
                    mix_input_1 = "[padded]"
                elif target_duration < v_dur:
                    # Trim voice to match target duration (with short fade out)
                    fade_start = max(0, target_duration - 0.5)
                    voice_part = f"[0:a]atrim=0:{target_duration},afade=t=out:st={fade_start}:d=0.5[trimmed];"
                    mix_input_1 = "[trimmed]"
            
            # 3. Combine
            # normalize=0 prevents amix from reducing volumes
            # duration=first ensures output matches the first input (voice/padded/trimmed voice)
            filter_complex = f"{voice_part}{bgm_part};{mix_input_1}[bgm]amix=inputs=2:duration=first:normalize=0"

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

    async def _get_audio_duration(self, audio_file: Path) -> float:
        """Get audio duration in seconds using ffprobe."""
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_file)
        ]
        result = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await result.communicate()
        return float(stdout.decode().strip())


# Singleton instance
audio_mixer = AudioMixer()
