import subprocess
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SubtitleService:
    """
    Generates ASS subtitle files with sentence-level timing.
    Uses ffprobe to measure audio durations.
    """

    def __init__(self):
        self.default_style = {
            "font_name": "Arial",
            "font_size": 24,
            "primary_color": "&H00FFFFFF",  # White
            "outline_color": "&H00000000",  # Black
            "back_color": "&H80000000",     # Semi-transparent black
            "bold": -1,
            "outline": 2,
            "shadow": 0,
            "alignment": 2,  # Bottom center
            "margin_v": 20
        }

    async def generate_subtitles(
        self,
        sentences: list[str],
        job_dir: Path,
        subtitle_style: dict = None
    ) -> Path:
        """
        Generate ASS subtitle file from sentences.

        Args:
            sentences: List of sentence strings
            job_dir: Job directory containing sentence audio files
            subtitle_style: Optional custom subtitle styling

        Returns:
            Path to subs.ass file
        """
        logger.info(f"Generating subtitles for {len(sentences)} sentences (job: {job_dir.name})")

        # Merge custom style with defaults
        style = {**self.default_style, **(subtitle_style or {})}

        # Get durations for each sentence audio file
        timings = []
        current_time = 0.0

        for i in range(len(sentences)):
            sentence_file = job_dir / f"sentence_{i+1:03d}.wav"
            duration = await self._get_audio_duration(sentence_file)

            timings.append({
                "start": current_time,
                "end": current_time + duration,
                "text": sentences[i]
            })

            current_time += duration

        # Generate ASS file
        subs_file = job_dir / "subs.ass"
        self._write_ass_file(subs_file, timings, style)

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

    def _write_ass_file(self, output_path: Path, timings: list[dict], style: dict):
        """
        Write ASS subtitle file.

        Args:
            output_path: Output file path
            timings: List of timing dicts with start, end, text
            style: Style configuration dict
        """
        # ASS file header
        ass_content = [
            "[Script Info]",
            "Title: Generated Subtitles",
            "ScriptType: v4.00+",
            "WrapStyle: 0",
            "PlayResX: 1920",
            "PlayResY: 1080",
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
