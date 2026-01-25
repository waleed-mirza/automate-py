import asyncio
import logging
from pathlib import Path
import tempfile

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
from src.utils.constants import MAX_SENTENCE_COUNT, CLEANUP_ON_FAILURE, CLEANUP_ON_SUCCESS

logger = logging.getLogger(__name__)


def _resolve_aspect_dimensions(aspect_ratio: str) -> tuple[int, int]:
    if aspect_ratio == "9:16":
        return 1080, 1920
    if aspect_ratio == "1:1":
        return 1080, 1080
    return 1920, 1080


async def process_job(job: RenderJob):
    """
    Process a single render job.

    Args:
        job: RenderJob to process
    """
    job_id = job.job_id
    job_manager = get_job_manager()
    job_dir = None
    job_succeeded = False

    try:
        # Update status to processing
        job_manager.update_job_status(job_id, "processing")
        logger.info(f"[{job_id}] Starting job processing")

        # Create job directory
        job_dir = Path(tempfile.gettempdir()) / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Process script into sentences
        logger.info(f"[{job_id}] Step 1: Processing script")
        sentences = script_processor.process(job.script)

        if not sentences:
            raise ValueError("Script processing resulted in no sentences")
        if len(sentences) > MAX_SENTENCE_COUNT:
            raise ValueError(
                f"Script produces too many sentences. Maximum {MAX_SENTENCE_COUNT} allowed."
            )

        # Step 2: Generate TTS voiceover
        logger.info(f"[{job_id}] Step 2: Generating voiceover")
        voice_file = await tts_service.generate_voiceover(sentences, job_dir)

        # Step 3: Upload voice.wav to S3
        logger.info(f"[{job_id}] Step 3: Uploading voiceover to S3")
        voice_url = await s3_uploader.upload_voice(voice_file, job_id)
        job_manager.update_job_status(job_id, "processing", voice_url=voice_url)

        # Step 4: Send webhook for voiceover upload
        logger.info(f"[{job_id}] Step 4: Sending voiceover_uploaded webhook")
        await webhook_service.send_voiceover_uploaded(job_id, voice_url)

        resolved_video_mode = getattr(job, "video_mode", None) or "base_video"
        resolved_aspect_ratio = getattr(job, "aspect_ratio", None) or "16:9"
        logger.info(f"[{job_id}] video_mode={resolved_video_mode}")
        logger.info(f"[{job_id}] aspect_ratio={resolved_aspect_ratio}")

        # Step 4: Generate images if video_mode is "generated_images"
        if resolved_video_mode == "generated_images":
            logger.info(f"[{job_id}] Step 4: Generating AI images from script")
            
            # Enhance prompts using GPT
            enhanced_prompts = await prompt_enhancement_service.enhance_prompts(sentences)
            
            # Generate images in parallel
            image_paths = await ai_thumbnail_service.generate_images_batch(
                enhanced_prompts,
                job_dir,
                aspect_ratio=resolved_aspect_ratio
            )
            
            # Get durations from generated sentence audio files
            durations = []
            for i in range(len(sentences)):
                sentence_file = job_dir / f"sentence_{i+1:03d}.wav"
                duration = await subtitle_service._get_audio_duration(sentence_file)
                durations.append(duration)
            
            # Store image paths for later use in rendering
            job.image_paths = image_paths
            job.image_durations = durations

        # Step 4.5: Download base video and get dimensions
        logger.info(f"[{job_id}] Step 4.5: Resolving video dimensions")
        base_video_path = None
        if resolved_video_mode == "generated_images":
            video_dimensions = _resolve_aspect_dimensions(resolved_aspect_ratio)
        else:
            base_video_path = await video_renderer.download_video(job.base_video_url, job_dir)
            video_dimensions = await video_renderer.get_video_dimensions(base_video_path)
            if video_dimensions is None:
                logger.warning(f"[{job_id}] Failed to get video dimensions, using default 1920x1080 for subtitles")
                video_dimensions = _resolve_aspect_dimensions("16:9")

        # Step 5: Generate subtitles
        logger.info(f"[{job_id}] Step 5: Generating subtitles")
        subtitle_file = await subtitle_service.generate_subtitles(
            sentences,
            job_dir,
            job.subtitle_style,
            video_dimensions
        )

        # Step 6: Mix audio (voice + BGM if provided)
        logger.info(f"[{job_id}] Step 6: Mixing audio")
        mixed_audio = await audio_mixer.mix_audio(
            voice_file,
            job_dir,
            job.bgm_url,
            target_duration=job.desired_duration
        )

        # Step 7: Render final video
        logger.info(f"[{job_id}] Step 7: Rendering video")
        if resolved_video_mode == "generated_images":
            if job.image_paths is None or job.image_durations is None:
                raise ValueError("Generated images or durations are missing for generated_images mode")
            final_video = await video_renderer.create_video_from_images(
                job.image_paths,
                job.image_durations,
                mixed_audio,
                subtitle_file,
                job_dir,
                job.resolution
                if job.resolution
                else f"{video_dimensions[0]}x{video_dimensions[1]}"
            )
        else:
            # Existing base video rendering
            if base_video_path is None:
                base_video_path = await video_renderer.download_video(job.base_video_url, job_dir)
            final_video = await video_renderer.render_video(
                base_video_path,
                mixed_audio,
                subtitle_file,
                job_dir,
                job.resolution,
                desired_duration=job.desired_duration
            )

        # Step 7.5: Generate thumbnail (non-blocking)
        thumbnail_url = None
        thumbnail_file = None
        is_short = False
        try:
            logger.info(f"[{job_id}] Step 7.5: Generating thumbnail")
            # Determine aspect ratio from video dimensions
            aspect_ratio = "16:9"  # default
            if video_dimensions:
                width, height = video_dimensions
                ratio = width / height
                if abs(ratio - 16/9) < 0.1:
                    aspect_ratio = "16:9"
                elif abs(ratio - 9/16) < 0.1:
                    aspect_ratio = "9:16"
                    is_short = True
                elif abs(ratio - 1.0) < 0.1:
                    aspect_ratio = "1:1"
            
            thumbnail_file = await thumbnail_service.generate_thumbnail(
                video_file=final_video,
                job_dir=job_dir,
                script=job.script,
                aspect_ratio=aspect_ratio,
                title=job.title
            )
            thumbnail_url = await s3_uploader.upload_thumbnail(thumbnail_file, job_id)
            logger.info(f"[{job_id}] Thumbnail uploaded successfully")
        except Exception as e:
            logger.warning(f"[{job_id}] Thumbnail generation failed: {str(e)}")

        # Step 7.6: Bake thumbnail into video for Shorts (YouTube workaround)
        if is_short and thumbnail_file and thumbnail_file.exists():
            try:
                logger.info(f"[{job_id}] Step 7.6: Baking thumbnail into Short video")
                final_video = await video_renderer.bake_thumbnail_into_video(
                    video_file=final_video,
                    thumbnail_file=thumbnail_file,
                    job_dir=job_dir
                )
                logger.info(f"[{job_id}] Thumbnail baked into Short video")
            except Exception as e:
                logger.warning(f"[{job_id}] Thumbnail baking failed (continuing without): {str(e)}")

        # Step 8: Upload subtitles and video to S3
        logger.info(f"[{job_id}] Step 8: Uploading subtitles and video to S3")
        subtitles_url = await s3_uploader.upload_subtitle(subtitle_file, job_id)
        video_url = await s3_uploader.upload_video(final_video, job_id)

        # Step 9: Send webhook for video completion
        logger.info(f"[{job_id}] Step 9: Sending video_completed webhook")
        await webhook_service.send_video_completed(
            job_id,
            voice_url,
            subtitles_url,
            video_url,
            thumbnail_url
        )

        # Update status to completed
        job_manager.update_job_status(
            job_id,
            "completed",
            voice_url=voice_url,
            subtitles_url=subtitles_url,
            video_url=video_url,
            thumbnail_url=thumbnail_url
        )

        logger.info(f"[{job_id}] Job completed successfully")
        job_succeeded = True

    except Exception as e:
        error_msg = str(e)
        logger.exception(f"[{job_id}] Job failed: {error_msg}")
        job_manager.update_job_status(job_id, "failed", error=error_msg)

    finally:
        # Cleanup job directory
        if job_dir:
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
