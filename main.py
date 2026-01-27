from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import asyncio

from config import settings
from src.api import routes
from src.utils.worker import start_workers

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Video Rendering Service",
    description="Python-based video rendering service with TTS voiceover and subtitle generation",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(routes.router)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    from src.utils.job_manager import get_job_manager

    job_manager = get_job_manager()

    return {
        "status": "healthy",
        "service": "video-rendering-service",
        "version": "1.0.0",
        "workers": {
            "active": len(app.state.worker_tasks) if hasattr(app.state, "worker_tasks") else 0,
            "max_concurrent_jobs": settings.max_concurrent_jobs
        },
        "queue": {
            "queued": job_manager.get_queue_size(),
            "processing": job_manager.get_active_jobs_count()
        }
    }


@app.on_event("startup")
async def startup_event():
    logger.info("Starting Video Rendering Service")
    logger.info(f"Max concurrent jobs: {settings.max_concurrent_jobs}")

    from src.utils.job_manager import get_job_manager

    job_manager = get_job_manager()
    await job_manager.initialize()

    # Start background workers
    app.state.worker_tasks = await start_workers(num_workers=settings.max_concurrent_jobs)
    logger.info(f"Started {len(app.state.worker_tasks)} background workers")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down Video Rendering Service")

    # Cancel background workers
    if hasattr(app.state, "worker_tasks"):
        for task in app.state.worker_tasks:
            task.cancel()

        # Wait for workers to finish
        await asyncio.gather(*app.state.worker_tasks, return_exceptions=True)
        logger.info("Background workers stopped")

    from src.utils.job_manager import get_job_manager

    job_manager = get_job_manager()
    await job_manager.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
