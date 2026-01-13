from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # Backblaze B2 configuration
    backblaze_bucket_name: str
    backblaze_key_id: str
    backblaze_application_key: str
    backblaze_endpoint_url: str

    # Webhook configuration
    webhook_url: str

    # Piper TTS configuration
    piper_bin_path: str = "/usr/local/bin/piper"
    piper_model_path: str = "/usr/local/share/piper/en_US-lessac-medium.onnx"

    # Job concurrency settings
    max_concurrent_jobs: int = 3

    # S3 upload path prefixes
    s3_voice_prefix: str = "uploads/voiceovers"
    s3_subtitle_prefix: str = "uploads/subtitles"
    s3_video_prefix: str = "uploads/renders"
    s3_thumbnail_prefix: str = "uploads/thumbnails"

    # Signed URL configuration (seconds)
    s3_signed_url_expiration_seconds: int = 3600


settings = Settings()
