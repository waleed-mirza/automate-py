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

# TTS settings
PIPER_THREADS = 2  # Limit Piper threads to avoid overload

# Subtitle defaults
DEFAULT_SUBTITLE_FONT = "Arial"
DEFAULT_SUBTITLE_SIZE = 18
DEFAULT_SUBTITLE_COLOR = "&H00FFFFFF"  # White
DEFAULT_SUBTITLE_OUTLINE = 2

# Webhook settings
WEBHOOK_TIMEOUT = 5.0  # Seconds
WEBHOOK_RETRY_ATTEMPTS = 1  # Retry attempts on webhook failure

# Cleanup settings
CLEANUP_ON_SUCCESS = True
CLEANUP_ON_FAILURE = True
