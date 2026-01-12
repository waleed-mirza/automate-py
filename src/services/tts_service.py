import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path

from config import settings
from src.utils.constants import PIPER_THREADS

logger = logging.getLogger(__name__)


class TTSService:
    """
    Text-to-Speech service using Piper TTS.
    Generates voiceover audio from text sentences.
    """

    def __init__(self):
        self.model_path = settings.piper_model_path
        self.piper_path: str | None = None

    def _resolve_piper_path(self) -> str:
        """
        Resolve the Piper binary path and ensure it is executable.
        """
        configured_path = getattr(settings, "piper_bin_path", None)
        if configured_path:
            path = Path(configured_path)
            if not path.exists():
                raise RuntimeError(
                    f"Piper TTS binary not found at configured path: {configured_path}"
                )
            if not os.access(path, os.X_OK):
                try:
                    current_mode = path.stat().st_mode
                    path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                except Exception as exc:
                    raise RuntimeError(
                        f"Piper TTS binary is not executable: {configured_path}. "
                        "Fix permissions (chmod +x) or update PIPER_BIN_PATH."
                    ) from exc
            return str(path)

        resolved = shutil.which("piper")
        if not resolved:
            raise RuntimeError(
                "Piper TTS not found. Please install Piper and ensure it's in your PATH. "
                f"Model path: {self.model_path}"
            )
        return resolved

    async def generate_voiceover(self, sentences: list[str], job_dir: Path) -> Path:
        """
        Generate voiceover audio from sentences.

        Args:
            sentences: List of sentence strings
            job_dir: Job directory path (e.g., /tmp/<job_id>/)

        Returns:
            Path to final voice.wav file
        """
        logger.info(f"Generating voiceover for {len(sentences)} sentences (job: {job_dir.name})")

        # Generate individual sentence audio files
        sentence_files = []
        for i, sentence in enumerate(sentences):
            sentence_file = job_dir / f"sentence_{i+1:03d}.wav"
            await self._generate_sentence_audio(sentence, sentence_file)
            sentence_files.append(sentence_file)
            logger.debug(f"Generated audio for sentence {i+1}/{len(sentences)}")

        # Concatenate all sentence files into voice.wav
        voice_file = job_dir / "voice.wav"
        await self._concatenate_audio(sentence_files, voice_file)

        logger.info(f"Voiceover generation complete: {voice_file}")
        return voice_file

    async def _generate_sentence_audio(self, text: str, output_path: Path):
        """
        Generate audio for a single sentence using Piper TTS.

        Args:
            text: Sentence text
            output_path: Output WAV file path
        """
        try:
            if not self.piper_path:
                self.piper_path = self._resolve_piper_path()
            # Run Piper TTS
            # Command: echo "text" | piper --model model.onnx --output_file output.wav
            cmd = [
                self.piper_path,
                "--model", self.model_path,
                "--output_file", str(output_path)
            ]

            env = os.environ.copy()
            env.update({
                "OMP_NUM_THREADS": str(PIPER_THREADS),
                "MKL_NUM_THREADS": str(PIPER_THREADS),
                "OPENBLAS_NUM_THREADS": str(PIPER_THREADS),
                "NUMEXPR_NUM_THREADS": str(PIPER_THREADS),
            })

            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env
            )

            stdout, stderr = process.communicate(input=text)

            if process.returncode != 0:
                raise RuntimeError(f"Piper TTS failed: {stderr}")

        except PermissionError as exc:
            raise RuntimeError(
                "Piper TTS is not executable. Check file permissions or set PIPER_BIN_PATH "
                f"to a valid executable. Current: {self.piper_path}"
            ) from exc
        except FileNotFoundError:
            raise RuntimeError(
                "Piper TTS not found. Please install Piper and ensure it's in your PATH. "
                f"Model path: {self.model_path}"
            )
        except Exception as e:
            raise RuntimeError(f"Failed to generate audio for sentence: {str(e)}")

    async def _concatenate_audio(self, audio_files: list[Path], output_path: Path):
        """
        Concatenate multiple audio files into a single file using FFmpeg.

        Args:
            audio_files: List of input audio file paths
            output_path: Output concatenated audio file path
        """
        if not audio_files:
            raise ValueError("No audio files to concatenate")

        if len(audio_files) == 1:
            # Copy the single file so per-sentence audio remains for subtitles
            shutil.copyfile(audio_files[0], output_path)
            return

        try:
            # Create concat file list for FFmpeg
            concat_file = output_path.parent / "concat_list.txt"
            with open(concat_file, "w") as f:
                for audio_file in audio_files:
                    # FFmpeg concat requires relative or absolute paths
                    f.write(f"file '{audio_file.absolute()}'\n")

            # Concatenate using FFmpeg
            cmd = [
                "ffmpeg",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-c", "copy",
                str(output_path),
                "-y"  # Overwrite output file
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            # Clean up concat file
            concat_file.unlink(missing_ok=True)

            logger.debug(f"Concatenated {len(audio_files)} audio files")

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"FFmpeg concatenation failed: {e.stderr}")
        except Exception as e:
            raise RuntimeError(f"Failed to concatenate audio: {str(e)}")


# Singleton instance
tts_service = TTSService()
