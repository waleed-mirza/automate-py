import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import logging
from pathlib import Path
from urllib.parse import urlparse

from config import settings

logger = logging.getLogger(__name__)


class S3Uploader:
    """
    Uploads files to Backblaze B2 (S3-compatible storage).
    Organizes uploads by asset type in separate directories.
    """

    def __init__(self):
        self.bucket_name = settings.backblaze_bucket_name
        self.client = self._create_s3_client()

    @staticmethod
    def _infer_region_from_endpoint(endpoint_url: str) -> str | None:
        """
        Best-effort extraction of region from Backblaze S3 endpoint.

        Example: https://s3.us-east-005.backblazeb2.com -> us-east-005
        """
        if not endpoint_url:
            return None

        try:
            host = urlparse(endpoint_url).hostname or ""
        except Exception:
            return None

        marker = "s3."
        suffix = ".backblazeb2.com"
        if host.startswith(marker) and host.endswith(suffix):
            return host[len(marker):-len(suffix)]

        return None

    def _create_s3_client(self):
        """Create and configure boto3 S3 client for Backblaze B2"""
        try:
            region_name = self._infer_region_from_endpoint(
                settings.backblaze_endpoint_url
            )
            client = boto3.client(
                "s3",
                endpoint_url=settings.backblaze_endpoint_url,
                aws_access_key_id=settings.backblaze_key_id,
                aws_secret_access_key=settings.backblaze_application_key,
                region_name=region_name,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                ),
            )
            logger.info(f"S3 client configured for bucket: {self.bucket_name}")
            return client
        except Exception as e:
            logger.error(f"Failed to create S3 client: {str(e)}")
            raise RuntimeError(f"S3 client initialization failed: {str(e)}")

    @staticmethod
    def is_s3_location(location: str) -> bool:
        """Return True when the location is an s3://bucket/key string."""
        return isinstance(location, str) and location.startswith("s3://")

    @staticmethod
    def _parse_s3_location(location: str) -> tuple[str, str]:
        """
        Parse an s3://bucket/key location into (bucket, key).

        Raises ValueError if the format is invalid.
        """
        if not S3Uploader.is_s3_location(location):
            raise ValueError("S3 location must start with s3://")

        stripped = location[len("s3://"):]
        parts = stripped.split("/", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError("S3 location must be in s3://bucket/key format")

        return parts[0], parts[1]

    def get_presigned_url(self, s3_location: str, expires_in: int | None = None) -> str:
        """
        Generate a presigned URL for an s3://bucket/key location.

        Args:
            s3_location: S3 location (s3://bucket/key)
            expires_in: Optional expiration override in seconds
        """
        bucket, key = self._parse_s3_location(s3_location)
        expiry = expires_in or settings.s3_signed_url_expiration_seconds

        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expiry,
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_msg = e.response.get("Error", {}).get("Message", str(e))
            logger.error(f"Presigned URL generation failed ({error_code}): {error_msg}")
            raise RuntimeError(f"Failed to generate presigned URL: {error_msg}")
        except Exception as e:
            logger.error(f"Unexpected error generating presigned URL: {str(e)}")
            raise RuntimeError(f"Presigned URL error: {str(e)}")

    async def upload_voice(self, file_path: Path, job_id: str) -> str:
        """
        Upload voice.wav to S3.

        Args:
            file_path: Path to voice.wav file
            job_id: Job ID for organizing files

        Returns:
            S3 location (s3://bucket/key)
        """
        object_key = f"{settings.s3_voice_prefix}/{job_id}-{file_path.name}"
        return await self._upload_file(file_path, object_key, "audio/wav")

    async def upload_subtitle(self, file_path: Path, job_id: str) -> str:
        """
        Upload subs.ass to S3.

        Args:
            file_path: Path to subs.ass file
            job_id: Job ID for organizing files

        Returns:
            S3 location (s3://bucket/key)
        """
        object_key = f"{settings.s3_subtitle_prefix}/{job_id}-{file_path.name}"
        return await self._upload_file(file_path, object_key, "text/plain")

    async def upload_video(self, file_path: Path, job_id: str) -> str:
        """
        Upload final.mp4 to S3.

        Args:
            file_path: Path to final.mp4 file
            job_id: Job ID for organizing files

        Returns:
            S3 location (s3://bucket/key)
        """
        object_key = f"{settings.s3_video_prefix}/{job_id}-{file_path.name}"
        return await self._upload_file(file_path, object_key, "video/mp4")

    async def upload_thumbnail(self, file_path: Path, job_id: str) -> str:
        """
        Upload thumbnail.jpg to S3.

        Args:
            file_path: Path to thumbnail image
            job_id: Job ID for organizing files

        Returns:
            S3 location (s3://bucket/key)
        """
        object_key = f"{settings.s3_thumbnail_prefix}/{job_id}-{file_path.name}"
        return await self._upload_file(file_path, object_key, "image/jpeg")

    async def _upload_file(
        self,
        file_path: Path,
        object_key: str,
        content_type: str
    ) -> str:
        """
        Upload a file to S3.

        Args:
            file_path: Local file path
            object_key: S3 object key (path in bucket)
            content_type: MIME type of file

        Returns:
            S3 location (s3://bucket/key)
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        try:
            logger.info(f"Uploading {file_path.name} to s3://{self.bucket_name}/{object_key}")

            # Upload file
            with open(file_path, "rb") as f:
                self.client.put_object(
                    Bucket=self.bucket_name,
                    Key=object_key,
                    Body=f,
                    ContentType=content_type,
                )

            s3_location = f"s3://{self.bucket_name}/{object_key}"

            logger.info("Upload successful (stored as S3 location)")
            return s3_location

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_msg = e.response.get("Error", {}).get("Message", str(e))
            logger.error(f"S3 upload failed ({error_code}): {error_msg}")
            raise RuntimeError(f"Failed to upload to S3: {error_msg}")
        except Exception as e:
            logger.error(f"Unexpected error during S3 upload: {str(e)}")
            raise RuntimeError(f"S3 upload error: {str(e)}")


# Singleton instance
s3_uploader = S3Uploader()
