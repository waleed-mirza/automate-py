"""
Application constants and resource limits.
"""

# Resource limits
MAX_SCRIPT_LENGTH = 50000  # Maximum characters in script
MAX_VIDEO_SIZE_MB = 500  # Maximum video file size in MB
MAX_AUDIO_SIZE_MB = 100  # Maximum audio file size in MB
DOWNLOAD_TIMEOUT_SECONDS = 60  # Timeout for downloading files
MAX_SENTENCE_COUNT = 500  # Maximum number of sentences to process

# FFmpeg settings
FFMPEG_PRESET = "fast"  # Balance between speed and quality (ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow)
AUDIO_BITRATE = "192k"  # Audio bitrate for output video

# Video sync settings (CRITICAL: These must be consistent across all components)
VIDEO_CROSSFADE_DURATION = 0.5  # Crossfade duration between image segments in seconds
# This value is used in: video_renderer.py, subtitle_service.py, tts_service.py
# Changing this requires updating all three files to maintain A/V sync

# TTS settings
PIPER_THREADS = 2  # Limit Piper threads to avoid overload

# Subtitle defaults
DEFAULT_SUBTITLE_FONT = "Noto Sans"  # Supports both English and Hindi (with Devanagari fallback)
DEFAULT_SUBTITLE_SIZE = 40
DEFAULT_SUBTITLE_COLOR = "&H00FFFFFF"  # White
DEFAULT_SUBTITLE_OUTLINE = 2
DEFAULT_SUBTITLE_MARGIN_V = 20
HORIZONTAL_SUBTITLE_SIZE_SCALE = 1.4
HORIZONTAL_SUBTITLE_MARGIN_SCALE = 1.4

# Webhook settings
WEBHOOK_TIMEOUT = 5.0  # Seconds
WEBHOOK_RETRY_ATTEMPTS = 1  # Retry attempts on webhook failure

# Cleanup settings
CLEANUP_ON_SUCCESS = True
CLEANUP_ON_FAILURE = True
