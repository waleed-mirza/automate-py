import shutil
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class FileManager:
    """
    Manages temporary files and cleanup.
    """

    @staticmethod
    def cleanup_job_directory(job_dir: Path):
        """
        Delete job directory and all its contents.

        Args:
            job_dir: Path to job directory (e.g., /tmp/<job_id>/)
        """
        if not job_dir.exists():
            logger.debug(f"Job directory does not exist, skipping cleanup: {job_dir}")
            return

        try:
            logger.info(f"Cleaning up job directory: {job_dir}")
            shutil.rmtree(job_dir)
            logger.info(f"Cleanup successful: {job_dir}")
        except Exception as e:
            logger.warning(f"Failed to cleanup job directory {job_dir}: {str(e)}")

    @staticmethod
    def get_directory_size(directory: Path) -> int:
        """
        Calculate total size of directory in bytes.

        Args:
            directory: Path to directory

        Returns:
            Total size in bytes
        """
        total_size = 0
        try:
            for file in directory.rglob("*"):
                if file.is_file():
                    total_size += file.stat().st_size
        except Exception as e:
            logger.warning(f"Error calculating directory size: {str(e)}")

        return total_size

    @staticmethod
    def format_size(size_bytes: int) -> str:
        """
        Format bytes to human-readable string.

        Args:
            size_bytes: Size in bytes

        Returns:
            Formatted string (e.g., "1.5 MB")
        """
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"


# Singleton instance
file_manager = FileManager()
