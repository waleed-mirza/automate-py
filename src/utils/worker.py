import asyncio
import logging
from pathlib import Path
import tempfile
import httpx

from src.utils.job_manager import get_job_manager, RenderJob
from src.services.script_processor import script_processor
from src.services.tts_service import tts_service
from src.services.subtitle_service import subtitle_service
from src.services.audio_mixer import audio_mixer
from src.services.video_renderer import video_renderer
from src.services.thumbnail_service import thumbnail_service
from src.services.prompt_enhancement_service import prompt_enhancement_service
from src.services.ai_thumbnail_service import ai_thumbnail_service
from src.services.webhook_service import webhook_service
from src.utils.s3_uploader import s3_uploader
from src.utils.file_manager import file_manager
from src.utils.constants import (
    MAX_SENTENCE_COUNT,
    CLEANUP_ON_FAILURE,
    CLEANUP_ON_SUCCESS,
    DOWNLOAD_TIMEOUT_SECONDS,
    MAX_AUDIO_SIZE_MB,
)

logger = logging.getLogger(__name__)


def _resolve_aspect_dimensions(aspect_ratio: str) -> tuple[int, int]:
    if aspect_ratio == "9:16":
        return 1080, 1920
    if aspect_ratio == "1:1":
        return 1080, 1080
    return 1920, 1080


async def _download_voiceover(voice_url: str, job_dir: Path, job_id: str) -> Path:
    resolved_url = voice_url
    if s3_uploader.is_s3_location(voice_url):
        resolved_url = s3_uploader.get_presigned_url(voice_url)

    voice_file = job_dir / "voice.wav"
    max_bytes = MAX_AUDIO_SIZE_MB * 1024 * 1024
    downloaded_bytes = 0

    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS) as client:
        async with client.stream("GET", resolved_url) as response:
            response.raise_for_status()
            with open(voice_file, "wb") as handle:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > max_bytes:
                        raise RuntimeError(
                            f"[{job_id}] Voiceover exceeds limit of {MAX_AUDIO_SIZE_MB} MB"
                        )
                    handle.write(chunk)

    return voice_file


async def process_job(job: RenderJob):
    """
    Process a single render job.

    Args:
        job: RenderJob to process
    """
    job_id = job.job_id
    job_manager = get_job_manager()
    job_dir = Path(tempfile.gettempdir()) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    job_succeeded = False
    voice_url = None
    subtitles_url = None
    video_url = None
    thumbnail_url = None
    voice_file: Path | None = None
    subtitle_file: Path | None = None
    final_video: Path | None = None
    base_video_path: Path | None = None
    thumbnail_file: Path | None = None
    video_dimensions: tuple[int, int] | None = None

    step_order = {
        "script": 1,
        "voiceover": 2,
        "voiceover_uploaded": 3,
        "voiceover_webhooked": 4,
        "images": 5,
        "video_dimensions": 6,
        "subtitles": 7,
        "mix_audio": 8,
        "render_video": 9,
        "assets_uploaded": 10,
        "thumbnail": 11,
        "video_completed": 12,
        "completed": 13,
    }

    try:
        job_status = await job_manager.get_job_status(job_id)

        if job_status and job_status.status == "completed":
            logger.info(f"[{job_id}] Job already completed, skipping")
            return

        step_name = job_status.step if job_status and job_status.step else ""
        step_index = step_order.get(step_name, 0)
        voice_url = job_status.voice_url if job_status else None
        subtitles_url = job_status.subtitles_url if job_status else None
        video_url = job_status.video_url if job_status else None
        thumbnail_url = job_status.thumbnail_url if job_status else None

        async def _update_status(status: str, step: str | None = None, **kwargs):
            nonlocal step_index
            next_step = step
            if step is not None:
                step_value = step_order.get(step, 0)
                if step_value <= step_index:
                    next_step = None
                else:
                    step_index = step_value
            await job_manager.update_job_status(
                job_id,
                status,
                step=next_step,
                **kwargs,
            )

        if voice_url and step_index < step_order["voiceover_uploaded"]:
            await _update_status(
                "processing",
                step="voiceover_uploaded",
                voice_url=voice_url,
            )

        if subtitles_url and video_url and step_index < step_order["assets_uploaded"]:
            await _update_status(
                "processing",
                step="assets_uploaded",
                voice_url=voice_url,
                subtitles_url=subtitles_url,
                video_url=video_url,
                thumbnail_url=thumbnail_url,
            )

        if thumbnail_url and step_index < step_order["thumbnail"]:
            await _update_status(
                "processing",
                step="thumbnail",
                thumbnail_url=thumbnail_url,
            )

        await _update_status("processing", step="processing")
        logger.info(f"[{job_id}] Starting job processing")

        logger.info(f"[{job_id}] Step 1: Processing script")
        await _update_status("processing", step="script")
        sentences = script_processor.process(job.script)

        if not sentences:
            raise ValueError("Script processing resulted in no sentences")
        if len(sentences) > MAX_SENTENCE_COUNT:
            raise ValueError(
                f"Script produces too many sentences. Maximum {MAX_SENTENCE_COUNT} allowed."
            )

        logger.info(f"[{job_id}] Step 2: Handling voiceover")
        await _update_status("processing", step="voiceover")

        if voice_url and step_index >= step_order["voiceover_uploaded"]:
            voice_file = job_dir / "voice.wav"
            if not voice_file.exists():
                logger.info(f"[{job_id}] Downloading existing voiceover")
                voice_file = await _download_voiceover(voice_url, job_dir, job_id)

            sentence_files = [
                job_dir / f"sentence_{i+1:03d}.wav" for i in range(len(sentences))
            ]
            if any(not path.exists() or path.stat().st_size == 0 for path in sentence_files):
                logger.info(f"[{job_id}] Regenerating sentence audio for subtitles")
                await tts_service.generate_voiceover(sentences, job_dir, language=job.language)
        else:
            logger.info(f"[{job_id}] Generating new voiceover")
            voice_file = await tts_service.generate_voiceover(sentences, job_dir, language=job.language)
            logger.info(f"[{job_id}] Uploading voiceover to S3")
            voice_url = await s3_uploader.upload_voice(voice_file, job_id)
            await _update_status(
                "processing",
                step="voiceover_uploaded",
                voice_url=voice_url,
            )
            step_index = step_order["voiceover_uploaded"]

        if voice_url and step_index < step_order["voiceover_webhooked"]:
            logger.info(f"[{job_id}] Sending voiceover_uploaded webhook")
            await webhook_service.send_voiceover_uploaded(job_id, voice_url)
            await _update_status(
                "processing",
                step="voiceover_webhooked",
                voice_url=voice_url,
            )
            step_index = step_order["voiceover_webhooked"]

        resolved_video_mode = getattr(job, "video_mode", None) or "base_video"
        resolved_aspect_ratio = getattr(job, "aspect_ratio", None) or "16:9"
        logger.info(f"[{job_id}] video_mode={resolved_video_mode}")
        logger.info(f"[{job_id}] aspect_ratio={resolved_aspect_ratio}")

        assets_ready = (
            subtitles_url is not None
            and video_url is not None
            and step_index >= step_order["assets_uploaded"]
        )

        if resolved_video_mode == "generated_images":
            video_dimensions = _resolve_aspect_dimensions(resolved_aspect_ratio)
            if not assets_ready:
                logger.info(f"[{job_id}] Step 3: Generating AI images")
                await _update_status("processing", step="images")
                enhanced_prompts = await prompt_enhancement_service.enhance_prompts(sentences)
                image_paths = await ai_thumbnail_service.generate_images_batch(
                    enhanced_prompts,
                    job_dir,
                    aspect_ratio=resolved_aspect_ratio,
                )

                # Calculate extended durations for natural pacing (reduced by 50%)
                lead_time = 0.25  # Viewer sees image before narration starts
                linger_time = 0.5  # Viewer processes visual after narration ends
                
                # IMPORTANT: Match durations to actual number of images generated
                num_images = len(image_paths)
                logger.info(f"[{job_id}] Generated {num_images} images from {len(sentences)} sentences")
                
                # If we have fewer images than sentences, truncate sentences to match
                # This ensures all arrays (images, durations, sentences for subtitles) are in sync
                if num_images < len(sentences):
                    logger.warning(
                        f"[{job_id}] Image generation partial failure: {num_images}/{len(sentences)}. "
                        f"Truncating sentences to match available images."
                    )
                    sentences = sentences[:num_images]
                
                durations = []
                for i in range(num_images):
                    sentence_file = job_dir / f"sentence_{i+1:03d}.wav"
                    if sentence_file.exists():
                        audio_duration = await subtitle_service._get_audio_duration(sentence_file)
                    else:
                        audio_duration = 5.0
                    
                    # Adaptive buffer based on sentence length (reduced by 50%)
                    if audio_duration < 3.0:
                        adaptive_buffer = 0.75  # Short sentences need more time
                    elif audio_duration < 6.0:
                        adaptive_buffer = 0.5  # Medium sentences
                    else:
                        adaptive_buffer = 0.25  # Long sentences
                    
                    extended_duration = audio_duration + lead_time + adaptive_buffer + linger_time
                    durations.append(extended_duration)
                    
                    logger.info(f"[{job_id}] Image {i+1}: audio={audio_duration:.2f}s, display={extended_duration:.2f}s")
                
                # Log sync verification
                logger.info(
                    f"[{job_id}] Sync check: {len(sentences)} sentences, "
                    f"{len(image_paths)} images, {len(durations)} durations"
                )

                job.image_paths = image_paths
                job.image_durations = durations
                job.lead_time = lead_time  # Store for subtitle generation
                await job_manager.update_job_payload(job)
        else:
            if not assets_ready:
                logger.info(f"[{job_id}] Step 3: Resolving base video")
                await _update_status("processing", step="video_dimensions")
                base_video_path = await video_renderer.download_video(job.base_video_url, job_dir)
                video_dimensions = await video_renderer.get_video_dimensions(base_video_path)
            if video_dimensions is None:
                logger.warning(
                    f"[{job_id}] Failed to get video dimensions, using default"
                )
                video_dimensions = _resolve_aspect_dimensions("16:9")

        if not assets_ready:
            logger.info(f"[{job_id}] Step 4: Generating subtitles and rendering video")
            await _update_status("processing", step="subtitles")
            
            # Generate subtitles based on video mode
            if resolved_video_mode == "generated_images":
                # Use extended durations for AI-generated images
                img_durations = getattr(job, "image_durations", None)
                lead_time = getattr(job, "lead_time", 0.25)
                if img_durations:
                    subtitle_file = await subtitle_service.generate_subtitles_with_extended_durations(
                        sentences,
                        job_dir,
                        img_durations,
                        lead_time,
                        job.subtitle_style,
                        video_dimensions,
                    )
                else:
                    # Fallback to standard subtitles
                    subtitle_file = await subtitle_service.generate_subtitles(
                        sentences,
                        job_dir,
                        job.subtitle_style,
                        video_dimensions,
                    )
            else:
                # Standard subtitles for base video mode
                subtitle_file = await subtitle_service.generate_subtitles(
                    sentences,
                    job_dir,
                    job.subtitle_style,
                    video_dimensions,
                )

            await _update_status("processing", step="mix_audio")
            if voice_file is None:
                raise RuntimeError("Missing voice file for audio mixing")
            
            # Calculate target duration and prepare voice audio
            if resolved_video_mode == "generated_images":
                img_durations = getattr(job, "image_durations", None)
                lead_time = getattr(job, "lead_time", 0.25)
                
                if img_durations:
                    target_duration = sum(img_durations)
                    logger.info(f"[{job_id}] Total video duration: {target_duration:.2f}s (extended for natural pacing)")
                    
                    # Create gapped voice audio with silence matching extended durations
                    voice_gapped = await tts_service.create_gapped_audio(
                        job_dir,
                        img_durations,
                        lead_time
                    )
                    voice_to_mix = voice_gapped
                else:
                    target_duration = job.desired_duration
                    voice_to_mix = voice_file
            else:
                target_duration = job.desired_duration
                voice_to_mix = voice_file
            
            mixed_audio = await audio_mixer.mix_audio(
                voice_to_mix,
                job_dir,
                job.bgm_url,
                target_duration=target_duration,
            )

            await _update_status("processing", step="render_video")
            if resolved_video_mode == "generated_images":
                img_paths = getattr(job, "image_paths", None)
                img_durations = getattr(job, "image_durations", None)
                if not img_paths or not img_durations:
                    raise ValueError("Missing images/durations for generated_images mode")
                final_video = await video_renderer.create_video_from_images(
                    img_paths,
                    img_durations,
                    mixed_audio,
                    subtitle_file,
                    job_dir,
                    job.resolution
                    if job.resolution
                    else f"{video_dimensions[0]}x{video_dimensions[1]}",
                )
            else:
                if base_video_path is None:
                    base_video_path = await video_renderer.download_video(
                        job.base_video_url,
                        job_dir,
                    )
                final_video = await video_renderer.render_video(
                    base_video_path,
                    mixed_audio,
                    subtitle_file,
                    job_dir,
                    job.resolution,
                    desired_duration=job.desired_duration,
                )

            logger.info(f"[{job_id}] Uploading new video and subtitles")
            subtitles_url = await s3_uploader.upload_subtitle(subtitle_file, job_id)
            video_url = await s3_uploader.upload_video(final_video, job_id)
            await _update_status(
                "processing",
                step="assets_uploaded",
                voice_url=voice_url,
                subtitles_url=subtitles_url,
                video_url=video_url,
                thumbnail_url=thumbnail_url,
            )
            step_index = step_order["assets_uploaded"]

        if subtitles_url is None or video_url is None:
            raise RuntimeError("Missing uploaded assets for completion")

        def _is_short_from_dimensions(dimensions: tuple[int, int]) -> bool:
            width, height = dimensions
            return abs((width / height) - (9 / 16)) < 0.1

        if video_dimensions is None:
            if final_video is None and video_url:
                final_video = await video_renderer.download_video(video_url, job_dir)
            if final_video is not None:
                video_dimensions = await video_renderer.get_video_dimensions(final_video)
            if video_dimensions is None:
                video_dimensions = _resolve_aspect_dimensions("16:9")

        is_short = _is_short_from_dimensions(video_dimensions)

        if thumbnail_url and step_index >= step_order["thumbnail"]:
            logger.info(f"[{job_id}] Using existing thumbnail")
        else:
            logger.info(f"[{job_id}] Step 5: Generating thumbnail")
            await _update_status("processing", step="thumbnail")
            try:
                if final_video is None:
                    final_video = await video_renderer.download_video(video_url, job_dir)

                aspect_ratio = "9:16" if is_short else "16:9"
                if abs((video_dimensions[0] / video_dimensions[1]) - 1.0) < 0.1:
                    aspect_ratio = "1:1"

                thumbnail_file = await thumbnail_service.generate_thumbnail(
                    video_file=final_video,
                    job_dir=job_dir,
                    script=job.script,
                    aspect_ratio=aspect_ratio,
                    title=job.title,
                )
                thumbnail_url = await s3_uploader.upload_thumbnail(thumbnail_file, job_id)
                await _update_status(
                    "processing",
                    step="thumbnail",
                    thumbnail_url=thumbnail_url,
                )
                step_index = step_order["thumbnail"]
                logger.info(f"[{job_id}] Thumbnail uploaded")
            except Exception as e:
                logger.warning(f"[{job_id}] Thumbnail generation failed: {str(e)}")

        if is_short and thumbnail_url:
            try:
                logger.info(f"[{job_id}] Step 6: Baking thumbnail into Short")
                if final_video is None:
                    final_video = await video_renderer.download_video(video_url, job_dir)
                thumbnail_path = thumbnail_file or (job_dir / "thumbnail.jpg")

                if final_video is not None and thumbnail_path.exists():
                    final_video = await video_renderer.bake_thumbnail_into_video(
                        video_file=final_video,
                        thumbnail_file=thumbnail_path,
                        job_dir=job_dir,
                    )
                    video_url = await s3_uploader.upload_video(final_video, job_id)
                    await _update_status(
                        "processing",
                        step="thumbnail",
                        video_url=video_url,
                    )
            except Exception as e:
                logger.warning(f"[{job_id}] Baking failed: {e}")

        if voice_url is None:
            raise RuntimeError("Missing voiceover for completion webhook")

        if step_index < step_order["video_completed"]:
            logger.info(f"[{job_id}] Sending completion webhook")
            await webhook_service.send_video_completed(
                job_id,
                voice_url,
                subtitles_url,
                video_url,
                thumbnail_url,
            )
            await _update_status(
                "processing",
                step="video_completed",
            )

        await _update_status(
            "completed",
            step="completed",
            voice_url=voice_url,
            subtitles_url=subtitles_url,
            video_url=video_url,
            thumbnail_url=thumbnail_url,
        )
        job_succeeded = True

    except Exception as e:
        error_msg = str(e)
        logger.exception(f"[{job_id}] Job failed: {error_msg}")
        await job_manager.update_job_status(job_id, "failed", error=error_msg)
        
        # Send failure webhook notification
        try:
            # Get current step from job status
            job_status = await job_manager.get_job_status(job_id)
            current_step = job_status.step if job_status else None
            error_type = "processing"
            
            # Categorize error type based on error message
            if "image" in error_msg.lower():
                error_type = "image_generation"
            elif "video" in error_msg.lower() or "render" in error_msg.lower():
                error_type = "video_rendering"
            elif "audio" in error_msg.lower() or "voiceover" in error_msg.lower():
                error_type = "voiceover"
            elif "upload" in error_msg.lower() or "s3" in error_msg.lower():
                error_type = "upload"
            elif "validation" in error_msg.lower() or "invalid" in error_msg.lower():
                error_type = "validation"
            
            await webhook_service.send_job_failed(
                job_id=job_id,
                error=error_msg,
                step=current_step,
                error_type=error_type
            )
        except Exception as webhook_error:
            logger.warning(f"[{job_id}] Failed to send failure webhook: {webhook_error}")

    finally:
        if job_succeeded and CLEANUP_ON_SUCCESS:
            file_manager.cleanup_job_directory(job_dir)
        elif not job_succeeded and CLEANUP_ON_FAILURE:
            file_manager.cleanup_job_directory(job_dir)


async def worker(worker_id: int):
    """
    Background worker that processes jobs from the queue.

    Args:
        worker_id: Worker identifier for logging
    """
    job_manager = get_job_manager()
    logger.info(f"Worker {worker_id} started")

    while True:
        try:
            # Wait for semaphore (limits concurrent jobs)
            async with job_manager.semaphore:
                # Get next job from queue
                job = await job_manager.get_next_job()
                logger.info(f"Worker {worker_id} picked up job {job.job_id}")

                # Process the job
                await process_job(job)

        except asyncio.CancelledError:
            logger.info(f"Worker {worker_id} cancelled")
            break
        except Exception as e:
            logger.exception(f"Worker {worker_id} unexpected error: {str(e)}")
            # Continue processing other jobs
            await asyncio.sleep(1)


async def start_workers(num_workers: int = 3):
    """
    Start background workers.

    Args:
        num_workers: Number of worker tasks to create
    """
    logger.info(f"Starting {num_workers} background workers")
    tasks = [asyncio.create_task(worker(i + 1)) for i in range(num_workers)]
    return tasks
