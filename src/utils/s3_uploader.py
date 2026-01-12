import boto3
from botocore.exceptions import ClientError
import logging
from pathlib import Path
from typing import Literal

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

    def _create_s3_client(self):
        """Create and configure boto3 S3 client for Backblaze B2"""
        try:
            client = boto3.client(
                "s3",
                endpoint_url=settings.backblaze_endpoint_url,
                aws_access_key_id=settings.backblaze_key_id,
                aws_secret_access_key=settings.backblaze_application_key,
            )
            logger.info(f"S3 client configured for bucket: {self.bucket_name}")
            return client
        except Exception as e:
            logger.error(f"Failed to create S3 client: {str(e)}")
            raise RuntimeError(f"S3 client initialization failed: {str(e)}")

    async def upload_voice(self, file_path: Path, job_id: str) -> str:
        """
        Upload voice.wav to S3.

        Args:
            file_path: Path to voice.wav file
            job_id: Job ID for organizing files

        Returns:
            Public URL to uploaded file
        """
        object_key = f"{settings.s3_voice_prefix}/{job_id}/voice.wav"
        return await self._upload_file(file_path, object_key, "audio/wav")

    async def upload_subtitle(self, file_path: Path, job_id: str) -> str:
        """
        Upload subs.ass to S3.

        Args:
            file_path: Path to subs.ass file
            job_id: Job ID for organizing files

        Returns:
            Public URL to uploaded file
        """
        object_key = f"{settings.s3_subtitle_prefix}/{job_id}/subs.ass"
        return await self._upload_file(file_path, object_key, "text/plain")

    async def upload_video(self, file_path: Path, job_id: str) -> str:
        """
        Upload final.mp4 to S3.

        Args:
            file_path: Path to final.mp4 file
            job_id: Job ID for organizing files

        Returns:
            Public URL to uploaded file
        """
        object_key = f"{settings.s3_video_prefix}/{job_id}/final.mp4"
        return await self._upload_file(file_path, object_key, "video/mp4")

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
            Public URL to uploaded file
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

            # Generate public URL
            url = f"{settings.backblaze_endpoint_url}/{self.bucket_name}/{object_key}"

            logger.info(f"Upload successful: {url}")
            return url

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
