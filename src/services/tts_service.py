from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path

from config import settings
from src.utils.constants import VIDEO_CROSSFADE_DURATION

try:
    from kokoro_onnx import Kokoro
except Exception:  # pragma: no cover - optional dependency handled at runtime
    Kokoro = None

try:
    import soundfile as sf
except Exception:  # pragma: no cover - optional dependency handled at runtime
    sf = None

logger = logging.getLogger(__name__)


class BaseTTSProvider:
    """
    Base class for TTS providers that generate per-sentence WAVs and a voice.wav.
    """

    async def generate_voiceover(
        self,
        sentences: list[str],
        job_dir: Path,
        language: str | None = None,
    ) -> Path:
        logger.info(f"Generating voiceover for {len(sentences)} sentences (job: {job_dir.name})")

        sentence_files = []
        for i, sentence in enumerate(sentences):
            sentence_file = job_dir / f"sentence_{i+1:03d}.wav"
            if sentence_file.exists() and sentence_file.stat().st_size > 0:
                logger.debug(f"Using cached audio for sentence {i+1}/{len(sentences)}")
            else:
                await self._generate_sentence_audio(sentence, sentence_file, language)
            sentence_files.append(sentence_file)
            logger.debug(f"Generated audio for sentence {i+1}/{len(sentences)}")

        voice_file = job_dir / "voice.wav"
        if voice_file.exists() and voice_file.stat().st_size > 0:
            logger.info(f"Using cached voiceover: {voice_file}")
            return voice_file
        await self._concatenate_audio(sentence_files, voice_file)

        logger.info(f"Voiceover generation complete: {voice_file}")
        return voice_file

    async def _generate_sentence_audio(
        self,
        text: str,
        output_path: Path,
        language: str | None = None,
    ):
        raise NotImplementedError

    async def _concatenate_audio(self, audio_files: list[Path], output_path: Path):
        if not audio_files:
            raise ValueError("No audio files to concatenate")

        if len(audio_files) == 1:
            shutil.copyfile(audio_files[0], output_path)
            return

        try:
            concat_file = output_path.parent / "concat_list.txt"
            with open(concat_file, "w") as f:
                for audio_file in audio_files:
                    f.write(f"file '{audio_file.absolute()}'\n")

            cmd = [
                "ffmpeg",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-c", "copy",
                str(output_path),
                "-y"
            ]

            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            concat_file.unlink(missing_ok=True)
            logger.debug(f"Concatenated {len(audio_files)} audio files")

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"FFmpeg concatenation failed: {e.stderr}")
        except Exception as e:
            raise RuntimeError(f"Failed to concatenate audio: {str(e)}")

    async def create_gapped_audio(
        self,
        job_dir: Path,
        extended_durations: list[float],
        lead_time: float,
        output_filename: str = "voice_gapped.wav"
    ) -> Path:
        """
        Create audio file with silence gaps matching extended image durations.
        
        IMPORTANT: Account for video crossfade overlap (0.5s between segments).
        The video shrinks due to crossfade, so audio segments must also be shorter
        to maintain sync.
        
        Args:
            job_dir: Job directory containing sentence audio files
            extended_durations: Extended duration for each image segment
            lead_time: Lead time before each sentence audio
            output_filename: Output filename
            
        Returns:
            Path to gapped audio file
        """
        output_path = job_dir / output_filename
        
        try:
            # Build filter complex to add silence gaps
            filter_parts = []
            input_args = []
            num_segments = len(extended_durations)
            
            for i, extended_dur in enumerate(extended_durations):
                sentence_file = job_dir / f"sentence_{i+1:03d}.wav"
                if not sentence_file.exists():
                    logger.warning(f"Sentence file not found: {sentence_file}")
                    continue
                
                input_args.extend(["-i", str(sentence_file)])
                
                # Calculate silence durations
                # extended_dur = lead_time + audio_duration + adaptive_buffer + linger_time
                # We need: lead_time silence + audio + (adaptive_buffer + linger_time) silence
                
                # Get actual audio duration
                probe_cmd = [
                    "ffprobe",
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(sentence_file)
                ]
                result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
                audio_duration = float(result.stdout.strip())
                
                # Account for crossfade overlap: each segment except the last is shortened
                # because the video segments overlap during crossfade
                effective_dur = extended_dur
                if i < num_segments - 1:
                    effective_dur -= VIDEO_CROSSFADE_DURATION
                
                # Calculate trailing silence
                trailing_silence = effective_dur - lead_time - audio_duration
                
                # Create filter: lead silence + audio + trailing silence
                # Use adelay for lead time, apad for trailing
                filter_parts.append(
                    f"[{i}:a]adelay={int(lead_time * 1000)}|{int(lead_time * 1000)},"
                    f"apad=whole_dur={effective_dur}[a{i}]"
                )
            
            if not filter_parts:
                raise ValueError("No valid sentence audio files found")
            
            # Concatenate all segments
            filter_complex = ";".join(filter_parts)
            concat_inputs = "".join(f"[a{i}]" for i in range(len(filter_parts)))
            filter_complex += f";{concat_inputs}concat=n={len(filter_parts)}:v=0:a=1[out]"
            
            cmd = [
                "ffmpeg",
                *input_args,
                "-filter_complex", filter_complex,
                "-map", "[out]",
                "-c:a", "pcm_s16le",
                str(output_path),
                "-y"
            ]
            
            logger.debug(f"Creating gapped audio with command: {' '.join(cmd)}")
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            logger.info(f"Created gapped audio: {output_path}")
            return output_path
            
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg gapped audio creation failed: {e.stderr}")
            raise RuntimeError(f"Failed to create gapped audio: {e.stderr}")
        except Exception as e:
            logger.error(f"Error creating gapped audio: {str(e)}")
            raise RuntimeError(f"Failed to create gapped audio: {str(e)}")


class PiperTTSProvider(BaseTTSProvider):
    """
    Text-to-Speech provider using Piper TTS.
    """

    def __init__(self):
        self.model_path = settings.piper_model_path
        self.piper_path: str | None = None
        self.threads = settings.piper_threads

    def _resolve_piper_path(self) -> str:
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

    async def _generate_sentence_audio(
        self,
        text: str,
        output_path: Path,
        language: str | None = None,
    ):
        try:
            if not self.piper_path:
                self.piper_path = self._resolve_piper_path()

            cmd = [
                self.piper_path,
                "--model", self.model_path,
                "--output_file", str(output_path)
            ]

            env = os.environ.copy()
            env.update({
                "OMP_NUM_THREADS": str(self.threads),
                "MKL_NUM_THREADS": str(self.threads),
                "OPENBLAS_NUM_THREADS": str(self.threads),
                "NUMEXPR_NUM_THREADS": str(self.threads),
            })

            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env
            )

            _, stderr = process.communicate(input=text)

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


class KokoroTTSProvider(BaseTTSProvider):
    """
    Text-to-Speech provider using kokoro-onnx.
    """

    def __init__(self):
        self.model_path = settings.kokoro_model_path
        self.voices_path = settings.kokoro_voices_path
        self.speaker = settings.kokoro_speaker
        self.speaker_en = settings.kokoro_speaker_en or settings.kokoro_speaker
        self.speaker_hi = settings.kokoro_speaker_hi
        self.threads = settings.kokoro_threads
        self._model: Kokoro | None = None

    @staticmethod
    def _is_hindi(text: str) -> bool:
        return any("\u0900" <= char <= "\u097F" for char in text)

    def _select_voice(self, text: str, language: str | None = None) -> str:
        if language:
            normalized = language.strip().lower()
            if normalized == "hi":
                return self.speaker_hi or self.speaker_en
            if normalized == "en":
                return self.speaker_en
        if self._is_hindi(text):
            return self.speaker_hi or self.speaker_en
        return self.speaker_en

    def _resolve_voices_file(self) -> Path:
        voices_path = Path(self.voices_path)
        if voices_path.is_dir():
            candidates = [
                voices_path / "voices-v1.0.bin",
                voices_path / "voices.bin",
                voices_path / "voices.npy",
            ]
            for candidate in candidates:
                if candidate.exists():
                    return candidate
            raise FileNotFoundError(
                f"No voices file found in {voices_path}. Expected one of: "
                f"{', '.join(c.name for c in candidates)}"
            )
        return voices_path

    def _ensure_model(self):
        if self._model:
            return
        if Kokoro is None or sf is None:
            raise RuntimeError(
                "Kokoro TTS dependencies are missing. Install kokoro-onnx and soundfile."
            )

        model_path = Path(self.model_path)
        if not model_path.exists():
            raise FileNotFoundError(
                f"Kokoro model file not found at {model_path}"
            )

        voices_file = self._resolve_voices_file()

        os.environ["OMP_NUM_THREADS"] = str(self.threads)
        os.environ["MKL_NUM_THREADS"] = str(self.threads)
        os.environ["OPENBLAS_NUM_THREADS"] = str(self.threads)
        os.environ["NUMEXPR_NUM_THREADS"] = str(self.threads)

        self._model = Kokoro(str(model_path), str(voices_file))

    async def _generate_sentence_audio(
        self,
        text: str,
        output_path: Path,
        language: str | None = None,
    ):
        try:
            self._ensure_model()
            if not self._model:
                raise RuntimeError("Kokoro model failed to load")

            voice = self._select_voice(text, language)
            
            # Determine language for Kokoro phonemizer
            kokoro_lang = 'en-us'  # default
            if language:
                normalized = language.strip().lower()
                if normalized == 'hi':
                    kokoro_lang = 'hi'  # Hindi language code
            elif self._is_hindi(text):
                kokoro_lang = 'hi'
            
            audio, sample_rate = self._model.create(text, voice=voice, lang=kokoro_lang)
            sf.write(str(output_path), audio, sample_rate, subtype="PCM_16")

        except Exception as e:
            raise RuntimeError(f"Failed to generate audio for sentence: {str(e)}")


class TTSService:
    """
    Text-to-Speech service with selectable providers.
    """

    def __init__(self):
        self._provider: BaseTTSProvider | None = None
        self._provider_name: str | None = None

    def _get_provider(self) -> BaseTTSProvider:
        provider_name = (settings.tts_provider or "piper").strip().lower()
        if provider_name != self._provider_name:
            if provider_name == "piper":
                self._provider = PiperTTSProvider()
            elif provider_name == "kokoro":
                self._provider = KokoroTTSProvider()
            else:
                raise RuntimeError(
                    f"Unknown TTS provider '{settings.tts_provider}'. Expected 'piper' or 'kokoro'."
                )
            self._provider_name = provider_name
            logger.info(f"TTS provider set to '{provider_name}'")
        return self._provider

    async def generate_voiceover(
        self,
        sentences: list[str],
        job_dir: Path,
        language: str | None = None,
    ) -> Path:
        provider = self._get_provider()
        return await provider.generate_voiceover(sentences, job_dir, language)

    async def create_gapped_audio(
        self,
        job_dir: Path,
        extended_durations: list[float],
        lead_time: float,
        output_filename: str = "voice_gapped.wav"
    ) -> Path:
        """
        Create audio file with silence gaps matching extended image durations.
        
        Args:
            job_dir: Job directory containing sentence audio files
            extended_durations: Extended duration for each image segment
            lead_time: Lead time before each sentence audio
            output_filename: Output filename
            
        Returns:
            Path to gapped audio file
        """
        provider = self._get_provider()
        return await provider.create_gapped_audio(job_dir, extended_durations, lead_time, output_filename)


tts_service = TTSService()
