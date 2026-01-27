import subprocess
import json
import logging
from pathlib import Path

from src.utils.constants import (
    DEFAULT_SUBTITLE_COLOR,
    DEFAULT_SUBTITLE_FONT,
    DEFAULT_SUBTITLE_MARGIN_V,
    DEFAULT_SUBTITLE_OUTLINE,
    DEFAULT_SUBTITLE_SIZE,
    HORIZONTAL_SUBTITLE_MARGIN_SCALE,
    HORIZONTAL_SUBTITLE_SIZE_SCALE,
    VIDEO_CROSSFADE_DURATION,
)

logger = logging.getLogger(__name__)


class SubtitleService:
    """
    Generates ASS subtitle files with sentence-level timing.
    Uses ffprobe to measure audio durations.
    """

    def __init__(self):
        self.default_style = {
            "font_name": DEFAULT_SUBTITLE_FONT,
            "font_size": DEFAULT_SUBTITLE_SIZE,
            "primary_color": DEFAULT_SUBTITLE_COLOR,  # White
            "outline_color": "&H00000000",  # Black
            "back_color": "&H80000000",     # Semi-transparent black
            "bold": -1,
            "outline": DEFAULT_SUBTITLE_OUTLINE,
            "shadow": 0,
            "alignment": 2,  # Bottom center
            "margin_v": DEFAULT_SUBTITLE_MARGIN_V
        }

    def _detect_script_and_get_font(self, text: str) -> str:
        """
        Detect if text contains Devanagari (Hindi) characters and return appropriate font.
        
        Devanagari Unicode range: U+0900 to U+097F
        This covers Hindi characters and numerals.
        
        Args:
            text: Text to analyze (typically a sentence or full script)
            
        Returns:
            Font name suitable for the detected script:
            - "Noto Sans Devanagari" for Hindi/Devanagari text
            - "Noto Sans" for English/Latin text
        """
        # Check if any character in text is in Devanagari Unicode range
        has_devanagari = any('\u0900' <= char <= '\u097F' for char in text)
        
        if has_devanagari:
            logger.debug("Devanagari script detected in text, using Noto Sans Devanagari font")
            return "Noto Sans Devanagari"
        else:
            logger.debug("Latin/English script detected in text, using Noto Sans font")
            return "Noto Sans"

    async def generate_subtitles_with_extended_durations(
        self,
        sentences: list[str],
        job_dir: Path,
        extended_durations: list[float],
        lead_time: float,
        subtitle_style: dict | None = None,
        video_dimensions: tuple[int, int] | None = None
    ) -> Path:
        """
        Generate ASS subtitle file with extended image durations and lead time.
        
        Args:
            sentences: List of sentence strings
            job_dir: Job directory containing sentence audio files
            extended_durations: Extended duration for each image segment
            lead_time: Lead time before audio starts in each segment
            subtitle_style: Optional custom subtitle styling
            video_dimensions: Optional (width, height) of the video
            
        Returns:
            Path to subs.ass file
        """
        logger.info(f"Generating subtitles with extended durations for {len(sentences)} sentences (job: {job_dir.name})")
        
        subs_file = job_dir / "subs.ass"
        
        # Merge custom style with defaults
        style = {**self.default_style, **(subtitle_style or {})}
        
        # Auto-detect script and set appropriate font if not explicitly provided
        if not subtitle_style or "font_name" not in subtitle_style:
            # Sample first sentence to detect script
            # This assumes all sentences are in the same language/script
            sample_text = sentences[0] if sentences else ""
            detected_font = self._detect_script_and_get_font(sample_text)
            style["font_name"] = detected_font
            logger.info(f"Auto-selected font '{detected_font}' based on script detection")


        # Adjust alignment and PlayRes if video dimensions provided
        play_res_x = 1920
        play_res_y = 1080

        if video_dimensions:
            width, height = video_dimensions
            play_res_x = width
            play_res_y = height

            # Only auto-set alignment if not explicitly provided in request
            if not subtitle_style or "alignment" not in subtitle_style:
                # Vertical/short video (height > width) -> Center vertically (Alignment 5)
                if height > width:
                    logger.info("Vertical video detected, setting subtitle alignment to center (5)")
                    style["alignment"] = 5
                else:
                    # Horizontal/square video -> Bottom center (Alignment 2)
                    logger.info("Horizontal/Square video detected, keeping default alignment (2)")
                    style["alignment"] = 2
                    if not subtitle_style or "font_size" not in subtitle_style:
                        style["font_size"] = int(round(style["font_size"] * HORIZONTAL_SUBTITLE_SIZE_SCALE))
                    if not subtitle_style or "margin_v" not in subtitle_style:
                        style["margin_v"] = int(round(style["margin_v"] * HORIZONTAL_SUBTITLE_MARGIN_SCALE))
            
            # Scale font size based on resolution (baseline 1080p)
            # scaled_size = int(style['font_size'] * play_res_y / 1080)
            if play_res_y != 1080:
                original_size = style["font_size"]
                scaled_size = int(original_size * play_res_y / 1080)
                # Ensure minimum readable size (e.g. 10)
                scaled_size = max(10, scaled_size)
                
                logger.info(f"Scaling font size from {original_size} to {scaled_size} (PlayResY: {play_res_y})")
                style["font_size"] = scaled_size

        # Get durations for each sentence audio file
        timings = []
        current_time = 0.0
        durations: list[float | None] = [None] * len(sentences)
        missing_indices: list[int] = []

        for i in range(len(sentences)):
            sentence_file = job_dir / f"sentence_{i+1:03d}.wav"
            if sentence_file.exists() and sentence_file.stat().st_size > 0:
                try:
                    durations[i] = await self._get_audio_duration(sentence_file)
                except Exception as exc:
                    logger.warning(
                        f"Failed to read duration for {sentence_file.name}: {exc}"
                    )
                    missing_indices.append(i)
            else:
                missing_indices.append(i)

        if missing_indices:
            fallback_total = None
            voice_file = job_dir / "voice.wav"
            if voice_file.exists() and voice_file.stat().st_size > 0:
                try:
                    fallback_total = await self._get_audio_duration(voice_file)
                except Exception as exc:
                    logger.warning(
                        f"Failed to read voiceover duration for fallback: {exc}"
                    )

            known_total = sum(d for d in durations if d is not None)
            if fallback_total is None:
                logger.warning(
                    "Missing sentence audio files and voiceover duration; using 5s fallback"
                )
                for index in missing_indices:
                    durations[index] = 5.0
            else:
                remaining = max(fallback_total - known_total, 0.0)
                weights = [self._sentence_weight(sentences[i]) for i in missing_indices]
                total_weight = sum(weights)
                if total_weight <= 0:
                    weights = [1] * len(missing_indices)
                    total_weight = len(missing_indices)

                if remaining <= 0:
                    average = fallback_total / max(len(sentences), 1)
                    for index in missing_indices:
                        durations[index] = average
                else:
                    for index, weight in zip(missing_indices, weights):
                        durations[index] = remaining * (weight / total_weight)

                logger.warning(
                    "Missing sentence audio files; using voiceover duration fallback"
                )

        for i, sentence in enumerate(sentences):
            duration = durations[i] if durations[i] is not None else 5.0
            
            timings.append({
                "start": current_time,
                "end": current_time + duration,
                "text": sentence
            })
            current_time += duration

        # Generate ASS file
        self._write_ass_file(subs_file, timings, style, play_res_x, play_res_y)

        logger.info(f"Subtitles generated: {subs_file}")
        return subs_file

    async def _get_audio_duration(self, audio_file: Path) -> float:
        """
        Get duration of audio file using ffprobe.

        Args:
            audio_file: Path to audio file

        Returns:
            Duration in seconds
        """
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                str(audio_file)
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            data = json.loads(result.stdout)
            duration = float(data["format"]["duration"])

            logger.debug(f"Audio duration for {audio_file.name}: {duration:.2f}s")
            return duration

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"ffprobe failed: {e.stderr}")
        except Exception as e:
            raise RuntimeError(f"Failed to get audio duration: {str(e)}")

    @staticmethod
    def _sentence_weight(sentence: str) -> int:
        words = [word for word in sentence.split() if word]
        return max(len(words), 1)

    def _write_ass_file(
        self,
        output_path: Path,
        timings: list[dict],
        style: dict,
        play_res_x: int = 1920,
        play_res_y: int = 1080
    ):
        """
        Write ASS subtitle file.

        Args:
            output_path: Output file path
            timings: List of timing dicts with start, end, text
            style: Style configuration dict
            play_res_x: PlayResX value
            play_res_y: PlayResY value
        """
        # ASS file header
        ass_content = [
            "[Script Info]",
            "Title: Generated Subtitles",
            "ScriptType: v4.00+",
            "WrapStyle: 0",
            f"PlayResX: {play_res_x}",
            f"PlayResY: {play_res_y}",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Default,{style['font_name']},{style['font_size']},{style['primary_color']},&H000000FF,{style['outline_color']},{style['back_color']},{style['bold']},0,0,0,100,100,0,0,1,{style['outline']},{style['shadow']},{style['alignment']},10,10,{style['margin_v']},1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
        ]

        # Add dialogue events
        for timing in timings:
            start_time = self._format_timestamp(timing["start"])
            end_time = self._format_timestamp(timing["end"])
            text = timing["text"].replace("\n", "\\N")  # Handle newlines

            dialogue = f"Dialogue: 0,{start_time},{end_time},Default,,0,0,0,,{text}"
            ass_content.append(dialogue)

        # Write file
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(ass_content))

    def _format_timestamp(self, seconds: float) -> str:
        """
        Format seconds to ASS timestamp format (H:MM:SS.CS).

        Args:
            seconds: Time in seconds

        Returns:
            Formatted timestamp string
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        centiseconds = int((seconds % 1) * 100)

        return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


# Singleton instance
subtitle_service = SubtitleService()
