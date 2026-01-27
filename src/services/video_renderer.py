import subprocess
import logging
import json
from pathlib import Path
import httpx

from src.utils.constants import (
    AUDIO_BITRATE,
    DOWNLOAD_TIMEOUT_SECONDS,
    FFMPEG_PRESET,
    MAX_VIDEO_SIZE_MB,
    VIDEO_CROSSFADE_DURATION,
)
from src.utils.s3_uploader import s3_uploader

logger = logging.getLogger(__name__)


class VideoRenderer:
    """
    Renders final video with burned subtitles and voiceover audio.
    """

    def __init__(self):
        self.ffmpeg_preset = FFMPEG_PRESET  # Balance between speed and quality

    async def render_video(
        self,
        video_source: str | Path,
        audio_file: Path,
        subtitle_file: Path,
        job_dir: Path,
        resolution: str | None = None,
        desired_duration: float | None = None
    ) -> Path:
        """
        Render final video with subtitles and audio.

        Args:
            video_source: URL to base video file OR Path to local video file
            audio_file: Path to audio file (voice or mixed)
            subtitle_file: Path to ASS subtitle file
            job_dir: Job directory for temporary files
            resolution: Optional resolution (e.g., "1920x1080")
            desired_duration: Optional desired video duration in seconds

        Returns:
            Path to final.mp4 file
        """
        logger.info(f"Rendering video (job: {job_dir.name})")

        # Download base video if URL provided, otherwise use local path
        if isinstance(video_source, Path):
            base_video = video_source
        else:
            base_video = await self.download_video(video_source, job_dir)

        # Handle video duration adjustment if requested
        if desired_duration:
            video_duration = await self._get_video_duration(base_video)
            logger.info(f"Video duration check: base={video_duration}s, desired={desired_duration}s")
            
            # Allow 1 second tolerance
            if abs(video_duration - desired_duration) > 1.0:
                if desired_duration > video_duration:
                    logger.info("Extending video to match desired duration")
                    base_video = await self.extend_video_with_crossfade(
                        base_video, desired_duration, job_dir
                    )
                else:
                    logger.info("Trimming video to match desired duration")
                    base_video = await self.trim_video(
                        base_video, desired_duration, job_dir
                    )

        # Render final video
        final_video = job_dir / "final.mp4"
        await self._render_with_ffmpeg(
            base_video,
            audio_file,
            subtitle_file,
            final_video,
            resolution,
            use_shortest=not bool(desired_duration)
        )

        logger.info(f"Video rendering complete: {final_video}")
        return final_video

    async def _get_video_duration(self, video_file: Path) -> float:
        """Get video duration using ffprobe."""
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_file)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return float(result.stdout.strip())
        except Exception as e:
            logger.warning(f"Failed to get video duration: {e}")
            return 0.0

    async def extend_video_with_crossfade(
        self,
        video_file: Path,
        target_duration: float,
        job_dir: Path
    ) -> Path:
        """
        Extend video to target duration by looping with crossfade transitions.
        
        Args:
            video_file: Path to base video
            target_duration: Desired duration in seconds
            job_dir: Job directory for output
            
        Returns:
            Path to extended video file
        """
        logger.info(f"Extending video to {target_duration}s with crossfade")
        output_file = job_dir / "extended_video.mp4"
        
        try:
            duration = await self._get_video_duration(video_file)
            if duration <= 1.5:
                # Too short for 1s crossfade, fallback to simple loop
                return await self._simple_loop_video(video_file, target_duration, job_dir)

            # Calculate required loops
            # effective_duration = duration - crossfade_duration (approx 1s)
            # We need target_duration
            loops = int(target_duration / (duration - 1)) + 2
            
            # Create a simple loop using stream_loop first (efficient) then trim
            # However, for crossfade we need a more complex filter
            # For simplicity and reliability, we'll use a concat approach with xfade
            # BUT generating a complex xfade filter for N loops is tricky.
            # Alternative: stream_loop then trim. It's not "crossfade" but it's seamless if video is seamless.
            # The prompt asks for "crossfade transitions".
            
            # Let's try to construct a complex filter for xfade.
            # Limit loops to avoid crazy command line length (max 10 loops usually enough for BG video)
            loops = min(loops, 20)
            
            # Prepare input args (same file repeated)
            inputs = []
            filter_parts = []
            
            # Start with video 0
            # [0:v]
            # Loop logic:
            # [0:v][1:v]xfade=duration=1:offset=(duration-1)[v1];
            # [v1][2:v]xfade=duration=1:offset=(2*duration-2)[v2];
            # ...
            
            # Actually easier: create a file with file list for concat demuxer if no crossfade
            # BUT prompt specified "crossfade".
            
            # If implementation of N-loop xfade is too complex, we can do:
            # 2-loop xfade, render to temp, then 2-loop that temp, etc. 
            # OR just standard loop if video is seamless.
            
            # Let's implement a simpler "tiling" with xfade using a loop in python to build the filter string.
            # We need to know exact duration for offsets.
            
            cmd = ["ffmpeg"]
            for _ in range(loops):
                cmd.extend(["-i", str(video_file)])
            
            filter_str = ""
            # Crossfade duration
            xf_dur = 1.0
            
            # We build the filter chain
            # [0:v][1:v]xfade=transition=fade:duration=1:offset=dur-1[v1];
            # [v1][2:v]xfade=transition=fade:duration=1:offset=new_offset[v2];
            
            current_offset = duration - xf_dur
            last_label = "0:v"
            
            # Since audio handling with xfade is complex (need afade/acrossfade too), 
            # and usually these are background videos without important audio (or audio is replaced),
            # we will focus on video stream. The render_video replaces audio anyway.
            
            for i in range(1, loops):
                next_label = f"v{i}"
                if i == loops - 1:
                    next_label = "vout"
                
                filter_str += f"[{last_label}][{i}:v]xfade=transition=fade:duration={xf_dur}:offset={current_offset:.3f}[{next_label}];"
                
                last_label = next_label
                current_offset += (duration - xf_dur)
            
            # Trim to exact target duration at the end
            filter_str += f"[vout]trim=duration={target_duration}[final]"
            
            cmd.extend([
                "-filter_complex", filter_str,
                "-map", "[final]",
                "-c:v", "libx264",
                "-preset", self.ffmpeg_preset,
                # Create silent audio to match video length if needed? 
                # render_video replaces audio, so we don't strictly need audio here.
                # But it's safer to output valid video file.
                "-an", # Remove audio from extended video (will be added by mixer)
                str(output_file),
                "-y"
            ])
            
            logger.debug(f"Extending video with command: {' '.join(cmd)}")
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            return output_file
            
        except subprocess.CalledProcessError as e:
            logger.warning(f"FFmpeg extend video failed: {e.stderr}")
            # Fallback: simple loop
            return await self._simple_loop_video(video_file, target_duration, job_dir)
        except Exception as e:
            logger.warning(f"Failed to extend video: {str(e)}")
            return video_file

    async def _simple_loop_video(self, video_file: Path, target_duration: float, job_dir: Path) -> Path:
        """Fallback: simple stream loop."""
        output_file = job_dir / "extended_simple.mp4"
        try:
            cmd = [
                "ffmpeg",
                "-stream_loop", "-1",
                "-i", str(video_file),
                "-t", str(target_duration),
                "-c:v", "copy",
                "-an",
                str(output_file),
                "-y"
            ]
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            return output_file
        except Exception:
            return video_file

    async def trim_video(
        self,
        video_file: Path,
        target_duration: float,
        job_dir: Path
    ) -> Path:
        """
        Trim video to target duration.
        
        Args:
            video_file: Path to base video
            target_duration: Desired duration in seconds
            job_dir: Job directory for output
            
        Returns:
            Path to trimmed video file
        """
        logger.info(f"Trimming video to {target_duration}s")
        output_file = job_dir / "trimmed_video.mp4"
        
        try:
            cmd = [
                "ffmpeg",
                "-i", str(video_file),
                "-t", str(target_duration),
                "-c:v", "libx264", # Re-encode to ensure exact cut and keyframes
                "-preset", self.ffmpeg_preset,
                "-an", # Remove audio (will be added by mixer)
                str(output_file),
                "-y"
            ]
            
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            return output_file
            
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg trim failed: {e.stderr}")
            raise RuntimeError(f"Failed to trim video: {e.stderr}")
        except Exception as e:
            logger.error(f"Failed to trim video: {str(e)}")
            raise RuntimeError(f"Failed to trim video: {str(e)}")

    async def get_video_dimensions(self, video_path: Path) -> tuple[int, int] | None:
        """
        Get video width and height using ffprobe.
        Handles rotation (swaps width/height if 90 or 270 degrees).

        Args:
            video_path: Path to video file

        Returns:
            Tuple of (width, height) or None if detection fails
        """
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,rotation:stream_tags=rotate",
                "-of", "json",
                str(video_path)
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            data = json.loads(result.stdout)
            if not data.get("streams"):
                logger.warning(f"No video streams found in {video_path.name}")
                return None
                
            stream = data["streams"][0]
            width = int(stream.get("width", 0))
            height = int(stream.get("height", 0))

            # Check for rotation
            rotation = 0
            # Try direct rotation field (some ffmpeg versions)
            if "rotation" in stream:
                rotation = int(float(stream["rotation"]))
            # Try tags
            elif "tags" in stream and "rotate" in stream["tags"]:
                rotation = int(float(stream["tags"]["rotate"]))
            # Try side_data_list (common in newer ffmpeg for phone videos)
            elif "side_data_list" in stream:
                for side_data in stream["side_data_list"]:
                    if "rotation" in side_data:
                        rotation = int(float(side_data["rotation"]))
                        break
            
            # Normalize rotation to 0-360
            rotation = rotation % 360
            
            # Swap dimensions if rotated 90 or 270 degrees
            if rotation in [90, 270]:
                width, height = height, width
                logger.info(f"Video detected with {rotation} deg rotation. Swapping dimensions to {width}x{height}")

            if width == 0 or height == 0:
                logger.warning(f"Invalid video dimensions detected: {width}x{height}")
                return None

            logger.debug(f"Video dimensions for {video_path.name}: {width}x{height}")
            return width, height

        except subprocess.CalledProcessError as e:
            logger.warning(f"ffprobe failed to get dimensions: {e.stderr}")
            return None
        except Exception as e:
            logger.warning(f"Failed to get video dimensions: {str(e)}")
            return None

    async def download_video(self, url: str, job_dir: Path) -> Path:
        """
        Download base video from URL.

        Args:
            url: URL to video file
            job_dir: Job directory for saving file

        Returns:
            Path to downloaded video file
        """
        resolved_url = url
        if s3_uploader.is_s3_location(url):
            resolved_url = s3_uploader.get_presigned_url(url)

        logger.info(f"Downloading base video: {resolved_url}")

        try:
            # Determine file extension from URL or default to mp4
            extension = ".mp4"
            if "." in url.split("/")[-1]:
                ext = url.split(".")[-1].split("?")[0]
                if ext.lower() in ["mp4", "mov", "avi", "mkv"]:
                    extension = "." + ext

            base_video = job_dir / f"base{extension}"
            if base_video.exists() and base_video.stat().st_size > 0:
                logger.info(f"Using cached base video: {base_video}")
                return base_video
            max_bytes = MAX_VIDEO_SIZE_MB * 1024 * 1024
            downloaded_bytes = 0

            # Stream download for large files
            async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS) as client:
                async with client.stream("GET", resolved_url) as response:
                    response.raise_for_status()

                    with open(base_video, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            downloaded_bytes += len(chunk)
                            if downloaded_bytes > max_bytes:
                                raise RuntimeError(
                                    f"Base video exceeds limit of {MAX_VIDEO_SIZE_MB} MB"
                                )
                            f.write(chunk)

            logger.debug(f"Downloaded base video: {base_video}")
            return base_video

        except httpx.HTTPError as e:
            raise RuntimeError(f"Failed to download base video: {str(e)}")
        except Exception as e:
            raise RuntimeError(f"Error downloading video: {str(e)}")

    async def _render_with_ffmpeg(
        self,
        video_file: Path,
        audio_file: Path,
        subtitle_file: Path,
        output_file: Path,
        resolution: str | None = None,
        use_shortest: bool = True
    ):
        """
        Render final video with FFmpeg.

        Args:
            video_file: Path to base video
            audio_file: Path to audio file
            subtitle_file: Path to subtitle file
            output_file: Path to output video
            resolution: Optional resolution string (e.g., "1920x1080")
            use_shortest: Whether to use -shortest flag (default True)
        """
        try:
            # Build video filter
            # Burn subtitles into video
            video_filter = f"ass={subtitle_file}"

            # Add scaling if resolution specified
            if resolution:
                video_filter = f"{video_filter},scale={resolution}"

            cmd = [
                "ffmpeg",
                "-i", str(video_file),
                "-i", str(audio_file),
                "-vf", video_filter,
                "-map", "0:v",  # Video from first input
                "-map", "1:a",  # Audio from second input
                "-c:v", "libx264",
                "-preset", self.ffmpeg_preset,
                "-c:a", "aac",
                "-b:a", AUDIO_BITRATE,
            ]
            
            if use_shortest:
                cmd.append("-shortest")  # End when shortest stream ends
                
            cmd.extend([
                str(output_file),
                "-y"  # Overwrite output file
            ])

            logger.debug(f"Running FFmpeg command: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            logger.debug("Video rendering successful")

        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"FFmpeg video rendering failed: {e.stderr}")
        except Exception as e:
            raise RuntimeError(f"Failed to render video: {str(e)}")

    async def create_video_from_images(
        self,
        image_paths: list[Path],
        durations: list[float],
        audio_file: Path,
        subtitle_file: Path,
        job_dir: Path,
        resolution: str | None = None
    ) -> Path:
        """
        Create video from image sequence with Ken Burns effect and crossfades.
        
        Args:
            image_paths: List of image file paths
            durations: Duration for each image (matches TTS audio segments)
            audio_file: Path to audio file
            subtitle_file: Path to ASS subtitle file
            job_dir: Job directory
            resolution: Optional resolution (e.g. "1920x1080")
            
        Returns:
            Path to final video file
        """
        logger.info(f"Creating video from {len(image_paths)} images")
        
        if len(image_paths) != len(durations):
            raise ValueError("Number of images must match number of durations")

        # Parse resolution or default
        width, height = 1920, 1080
        if resolution:
            try:
                w, h = map(int, resolution.split("x"))
                width, height = w, h
            except ValueError:
                logger.warning(f"Invalid resolution format: {resolution}, using default 1920x1080")

        # 1. Create video segments
        segment_files = []
        try:
            for i, (img_path, duration) in enumerate(zip(image_paths, durations)):
                segment_file = job_dir / f"segment_{i:03d}.mp4"
                
                # Calculate frames for zoompan (assuming 30fps)
                # Ensure duration is at least slightly longer than crossfade (0.5s) if possible
                # But we must follow the duration spec.
                frames = int(duration * 30)
                
                # Ken Burns effect: zoom in
                # zoompan=z='min(zoom+0.0015,1.5)':d={duration*30}:s=WxH
                zoompan_filter = (
                    f"zoompan=z='min(zoom+0.0015,1.5)':d={frames}:s={width}x{height},"
                    f"format=yuv420p"
                )
                
                cmd = [
                    "ffmpeg",
                    "-loop", "1",
                    "-i", str(img_path),
                    "-vf", zoompan_filter,
                    "-t", str(duration),
                    "-c:v", "libx264",
                    "-preset", self.ffmpeg_preset,
                    "-r", "30",
                    "-an",
                    str(segment_file),
                    "-y"
                ]
                
                subprocess.run(cmd, capture_output=True, text=True, check=True)
                segment_files.append(segment_file)

            # 2 & 3. Combine segments with crossfade
            combined_video = job_dir / "combined_segments.mp4"
            
            if len(segment_files) == 1:
                # Just copy the single segment
                cmd = [
                    "ffmpeg", "-i", str(segment_files[0]),
                    "-c", "copy", str(combined_video), "-y"
                ]
                subprocess.run(cmd, capture_output=True, text=True, check=True)
            else:
                # Build complex filter for xfade
                inputs = []
                for f in segment_files:
                    inputs.extend(["-i", str(f)])
                
                # Iterate to build filter chain
                filter_chain = ""
                current_offset = 0.0
                crossfade_dur = VIDEO_CROSSFADE_DURATION
                last_label = "0:v"
                
                for i in range(1, len(segment_files)):
                    prev_original_dur = durations[i-1]
                    
                    if i == 1:
                        transition_offset = prev_original_dur - crossfade_dur
                    else:
                        transition_offset = current_offset + prev_original_dur - crossfade_dur
                    
                    next_label = f"v{i}"
                    if i == len(segment_files) - 1:
                        next_label = "v_out"
                        
                    filter_chain += (
                        f"[{last_label}][{i}:v]xfade=transition=fade:"
                        f"duration={crossfade_dur}:offset={transition_offset:.3f}[{next_label}];"
                    )
                    
                    last_label = next_label
                    current_offset = transition_offset
                
                cmd = ["ffmpeg"] + inputs + [
                    "-filter_complex", filter_chain.rstrip(";"),
                    "-map", f"[{last_label}]",
                    "-c:v", "libx264",
                    "-preset", self.ffmpeg_preset,
                    "-an",
                    str(combined_video),
                    "-y"
                ]
                
                logger.debug(f"Combining segments with xfade: {' '.join(cmd)}")
                subprocess.run(cmd, capture_output=True, text=True, check=True)

            # 4 & 5 & 6. Final render with subtitles and audio
            final_video = job_dir / "final.mp4"
            await self._render_with_ffmpeg(
                combined_video,
                audio_file,
                subtitle_file,
                final_video,
                resolution,
                use_shortest=True # Audio might be longer/shorter, fit to video usually? Or use_shortest=True ensures we stop when video stops
            )
            
            return final_video

        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg failed in create_video_from_images: {e.stderr}")
            raise RuntimeError(f"Failed to create video from images: {e.stderr}")
        except Exception as e:
            logger.error(f"Error in create_video_from_images: {str(e)}")
            raise

    async def bake_thumbnail_into_video(
        self,
        video_file: Path,
        thumbnail_file: Path,
        job_dir: Path,
    ) -> Path:
        """
        Prepend thumbnail as a short frame at the start of the video.
        This is the workaround for YouTube Shorts not supporting custom thumbnails via API.
        YouTube will auto-select this frame as the thumbnail.

        Args:
            video_file: Path to the rendered video
            thumbnail_file: Path to the thumbnail image
            job_dir: Job directory for temporary files

        Returns:
            Path to the modified video with baked thumbnail
        """
        logger.info(f"Baking thumbnail into video for Shorts")

        output_file = job_dir / "final_with_thumb.mp4"

        try:
            # Get video stream parameters (dimensions, fps, pixel format)
            video_probe_cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate,pix_fmt,sample_aspect_ratio",
                "-of", "json",
                str(video_file)
            ]
            video_probe_result = subprocess.run(video_probe_cmd, capture_output=True, text=True, check=True)
            video_probe_data = json.loads(video_probe_result.stdout)
            video_stream = video_probe_data["streams"][0]
            
            width = video_stream["width"]
            height = video_stream["height"]
            fps_val = video_stream["r_frame_rate"]
            pix_fmt = video_stream.get("pix_fmt", "yuv420p")

            # Get audio stream parameters (sample rate, channels)
            audio_probe_cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=sample_rate,channels",
                "-of", "json",
                str(video_file)
            ]
            audio_probe_result = subprocess.run(audio_probe_cmd, capture_output=True, text=True, check=True)
            audio_probe_data = json.loads(audio_probe_result.stdout)
            
            sample_rate = 44100
            channels = "stereo"
            if audio_probe_data.get("streams"):
                audio_stream = audio_probe_data["streams"][0]
                sample_rate = int(audio_stream.get("sample_rate", 44100))
                num_channels = int(audio_stream.get("channels", 2))
                channels = "stereo" if num_channels >= 2 else "mono"

            # Single-pass concat filter approach
            # 1. Scale/pad thumbnail to match video dimensions and FPS
            # 2. Generate silent audio for thumbnail segment
            # 3. Setsar on main video to match
            # 4. Concat everything
            filter_complex = (
                f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps_val}[v0];"
                f"anullsrc=r={sample_rate}:cl={channels}:d=0.5[a0];"
                f"[1:v]setsar=1[v1];"
                f"[v0][a0][v1][1:a]concat=n=2:v=1:a=1[v][a]"
            )

            cmd = [
                "ffmpeg",
                "-loop", "1",
                "-t", "0.5",
                "-i", str(thumbnail_file),
                "-i", str(video_file),
                "-filter_complex", filter_complex,
                "-map", "[v]",
                "-map", "[a]",
                "-c:v", "libx264",
                "-preset", self.ffmpeg_preset,
                "-pix_fmt", pix_fmt,
                "-c:a", "aac",
                "-b:a", AUDIO_BITRATE,
                "-movflags", "+faststart",
                str(output_file),
                "-y"
            ]
            
            logger.debug(f"Baking thumbnail with command: {' '.join(cmd)}")
            subprocess.run(cmd, capture_output=True, text=True, check=True)

            logger.info(f"Thumbnail baked into video: {output_file}")
            return output_file

        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg thumbnail baking failed: {e.stderr}")
            raise RuntimeError(f"FFmpeg thumbnail baking failed: {e.stderr}")
        except Exception as e:
            logger.error(f"Failed to bake thumbnail into video: {str(e)}")
            raise RuntimeError(f"Failed to bake thumbnail into video: {str(e)}")


# Singleton instance
video_renderer = VideoRenderer()
