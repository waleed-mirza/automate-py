# TTS Model Upgrade Research Plan

## Current Implementation Summary

**Current TTS**: Piper with `en_US-lessac-medium.onnx`
- Model size: ~80-100MB
- Voice: Lessac (medium quality, somewhat robotic)
- Resource usage: 2 threads, minimal RAM (~200-300MB)
- Format: ONNX (CPU-optimized)
- Location: `/usr/local/share/piper/en_US-lessac-medium.onnx`
- Integration: src/services/tts_service.py

**Target Deployment**: $4-6 VPS droplets with 512MB-1GB RAM, 3 concurrent jobs max

## Better TTS Options Analysis

### Top Candidates for Low-Resource VPS

#### 1. **Kokoro-82M** ⭐ PRIMARY RECOMMENDATION

**Why it's better:**
- Near-human voice quality despite being lightweight
- 82M parameters (10x smaller than many modern TTS models)
- Decoder-only architecture = faster synthesis
- No encoders or diffusion = minimal latency

**Resource Requirements:**
- Model size: ~350MB
- RAM: 2GB recommended (can run on less with swap)
- CPU: Multi-core, achieves 3-11× real-time speed
- Disk: ~400MB total with voice files

**Voice Quality:**
- Comparable to much larger models
- Clear, expressive, natural intonation
- Multiple speaker voices available

**Deployment:**
- Docker available: `ghcr.io/remsky/kokoro-fastapi-cpu:v0.2.0post4`
- Python library: `pip install kokoro-onnx`
- ONNX format for CPU efficiency
- Apache 2.0 license (commercial use permitted)

**Trade-offs:**
- No voice cloning capability
- Decoder-only may limit some expressive controls
- Requires slightly more RAM than Piper (2GB vs <1GB)

#### 2. **MeloTTS** - ALTERNATIVE IF UPGRADING RAM

**Why it's better:**
- Specifically optimized for CPU real-time inference
- Natural, fluid speech with clear articulation
- Most downloaded TTS on Hugging Face
- Multi-language support (bonus feature)

**Resource Requirements:**
- Model size: 180MB (smaller than Kokoro!)
- RAM: 4GB minimum recommended
- CPU: Multi-core processor sufficient
- Disk: ~200MB total

**Voice Quality:**
- Natural and fluid across multiple languages
- Clear articulation
- Supports American, British, Indian, Australian English

**Deployment:**
- Easy: `pip install melotts`
- Docker recommended for Windows
- Can run on Raspberry Pi
- MIT License (commercial use permitted)

**Trade-offs:**
- **Requires 4GB RAM minimum** (may not work on 512MB droplets even with swap)
- No voice cloning
- May need droplet upgrade to $12/month (4GB tier)

#### 3. **Chatterbox-Turbo** - HIGH QUALITY OPTION

**Why it's better:**
- Sub-200ms inference latency
- High-fidelity audio benchmarked against ElevenLabs
- Voice cloning from reference clips
- Emotion control features

**Resource Requirements:**
- Model size: 350M parameters
- RAM: Likely 2-4GB (not explicitly documented)
- Lower VRAM than standard models
- Distilled decoder for efficiency

**Voice Quality:**
- High-fidelity output
- Emotion exaggeration control
- Natural paralinguistics ([laugh], [chuckle])

**Trade-offs:**
- English-only
- Includes imperceptible watermarks
- May be overkill for basic narration use case
- Resource requirements uncertain for 512MB droplets

## Resource Constraint Analysis

**Your Current Droplets:**
- 512MB-1GB RAM + swap
- Max 3 concurrent jobs
- $4-6/month budget

**Viability Assessment:**

| Model | 512MB Droplet | 1GB Droplet | 4GB Droplet | Est. Cost |
|-------|---------------|-------------|-------------|-----------|
| Piper (current) | ✅ Works | ✅ Works | ✅ Works | $4-6 |
| Kokoro-82M | ⚠️ Tight with swap | ✅ Should work | ✅ Comfortable | $4-6 |
| MeloTTS | ❌ Insufficient | ❌ Insufficient | ✅ Works | $12+ |
| Chatterbox | ❌ Uncertain | ⚠️ Uncertain | ✅ Likely works | $12+ |

## Recommendations

### Option A: Upgrade to Kokoro-82M (Keep Current Droplet Size)

**Best for:** Significantly better voice quality without increasing costs

**Implementation approach:**
1. Replace Piper binary calls with Kokoro Python library
2. Keep ONNX format for CPU efficiency
3. Test with 1 concurrent job first, then scale to 3
4. May need to reduce concurrent jobs to 2 if RAM becomes tight

**Estimated effort:** 4-6 hours (moderate complexity)

**Pros:**
- Much more human-like voices
- Same deployment costs
- Minimal architectural changes
- ONNX format similar to current Piper setup

**Cons:**
- Requires testing on actual droplet to confirm RAM usage
- May need to reduce concurrent jobs from 3 to 2
- Slightly slower than Piper (but still real-time)

### Option B: Upgrade Droplet to 4GB + Use MeloTTS

**Best for:** Maximum quality with minimal code changes

**Implementation approach:**
1. Upgrade VPS to 4GB RAM tier (~$12/month)
2. Install MeloTTS via pip
3. Replace TTS service calls
4. Can maintain 3 concurrent jobs comfortably

**Estimated effort:** 3-4 hours (simpler integration)

**Pros:**
- Smallest model size (180MB)
- Excellent CPU optimization
- Very natural voice quality
- Multi-language support (future-proof)
- Comfortable RAM headroom

**Cons:**
- Doubles hosting costs ($6 → $12/month per droplet)
- May be unnecessary expense if Kokoro works

### Option C: Keep Piper, Upgrade to Better Voice Model

**Best for:** Minimal risk, incremental improvement

**Implementation approach:**
1. Download higher-quality Piper voice (e.g., `en_US-amy-high`)
2. Update `PIPER_MODEL_PATH` in .env
3. Test voice quality vs current lessac-medium
4. Zero code changes required

**Estimated effort:** 30 minutes

**Pros:**
- Zero code changes
- Same resource usage
- Instant rollback if quality isn't better
- No cost increase

**Cons:**
- Limited improvement (still Piper ecosystem)
- May not deliver "human-like" quality you're seeking
- Diminishing returns vs switching to Kokoro

## Implementation Plan (Option A - Kokoro-82M)

### Phase 1: Setup and Testing (Local/Staging)

**Files to modify:**
- `requirements.txt` - Add Kokoro library
- `config.py` - Add Kokoro configuration options
- `src/services/tts_service.py` - Replace Piper calls with Kokoro
- `.env.example` - Document new environment variables
- `Dockerfile` - Update base image if needed

**Configuration changes:**
```
KOKORO_MODEL_PATH=/usr/local/share/kokoro/kokoro-v0_19.onnx
KOKORO_VOICES_PATH=/usr/local/share/kokoro/voices
KOKORO_SPEAKER=af_bella  # or other available voice
KOKORO_THREADS=2
```

**Steps:**
1. Test Kokoro locally first (Docker or Python)
2. Compare voice quality side-by-side with Piper
3. Benchmark RAM usage with 1 sentence vs 50 sentences
4. Test concurrent job handling (1, 2, 3 jobs)

### Phase 2: Integration

**Core changes in `src/services/tts_service.py`:**

1. Replace Piper subprocess calls with Kokoro Python API
2. Keep per-sentence WAV generation for subtitle timing
3. Maintain same concatenation logic (FFmpeg)
4. Preserve error handling and logging patterns

**Backward compatibility:**
- Add `TTS_PROVIDER` env var to switch between Piper/Kokoro
- Keep Piper code path available for fallback
- Allow A/B testing in production

### Phase 3: Docker/Deployment Updates

**Dockerfile changes:**
1. Download Kokoro model files during image build
2. Verify model files are accessible
3. Keep image size reasonable (<2GB total)
4. Test startup time impact

**Docker Compose:**
1. Update memory limits if needed
2. Add health check for Kokoro model loading
3. Mount model files as volume (optional, for easier updates)

### Phase 4: Production Testing

**Gradual rollout:**
1. Deploy to 1 droplet first
2. Monitor RAM usage over 24 hours
3. Test with peak concurrent jobs (3 simultaneous)
4. Compare processing times vs Piper baseline
5. Verify no OOM kills with swap enabled

**Monitoring metrics:**
- RAM usage per job
- Processing time per sentence
- Total job completion time
- Swap usage patterns
- Error rates

### Phase 5: Optimization (If Needed)

**If RAM is tight:**
- Reduce `MAX_CONCURRENT_JOBS` from 3 to 2
- Implement model unloading between jobs
- Add queue priority for smaller scripts
- Consider lazy model loading

**If speed is slower:**
- Optimize thread count (`KOKORO_THREADS`)
- Pre-warm model on service startup
- Cache speaker embeddings if available

## Verification & Testing Plan

### Voice Quality Testing

**Comparison methodology:**
1. Generate same script with Piper (current) and Kokoro
2. Upload both to temp S3 location
3. A/B test with team/users
4. Rate on scale: naturalness, clarity, emotion, pacing

**Test scripts:**
- Short (1 sentence) - verify quality
- Medium (10 sentences) - verify consistency
- Long (50+ sentences) - verify no quality degradation
- Edge cases: numbers, punctuation, long words

### Resource Testing

**RAM usage verification:**
```bash
# Monitor RAM during job processing
docker stats --no-stream

# Check for OOM kills
dmesg | grep -i "out of memory"

# Monitor swap usage
free -h
```

**Concurrent job stress test:**
1. Submit 6 jobs simultaneously (queue 3, backlog 3)
2. Monitor RAM peaks during processing
3. Verify no crashes or OOM kills
4. Check job completion times

### Integration Testing

**Endpoints to test:**
- `POST /voiceover` - TTS generation + S3 upload
- `POST /render` - Full pipeline with new voice
- `POST /render-video` - Manual render (subtitle timing accuracy)

**Verify:**
- All S3 uploads succeed
- Subtitle timing remains accurate
- FFmpeg concatenation works
- Webhook notifications sent correctly
- Error handling graceful

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Kokoro uses too much RAM | Medium | High | Test on staging first, keep Piper fallback |
| Voice quality not much better | Low | Medium | Do side-by-side testing before full rollout |
| Processing time too slow | Low | Medium | Benchmark before deployment, optimize threads |
| Model loading fails in Docker | Low | High | Verify model paths in Dockerfile, add health checks |
| Concurrent jobs cause OOM | Medium | High | Reduce MAX_CONCURRENT_JOBS to 2 if needed |
| Breaking change in API | Low | High | Keep Piper as fallback, use TTS_PROVIDER toggle |

## User Decisions

✅ **Budget:** Keep current $4-6 droplets (no upgrade)
✅ **TTS Model:** Use Kokoro-82M (Option A)
✅ **Fallback:** Keep dual TTS support (Piper + Kokoro)
✅ **Testing:** Local Docker testing, then direct production deployment

## Final Recommendation: Kokoro-82M with Dual TTS Support

**Implementation approach:**
- Add Kokoro-82M as primary TTS provider
- Keep Piper as fallback option
- Use `TTS_PROVIDER` environment variable to switch between them
- Default to Kokoro for new deployments
- Test locally with Docker before production rollout

**Expected outcomes:**
- Significantly more human-like voice quality
- Same hosting costs ($4-6/month)
- May need to reduce concurrent jobs from 3 to 2 if RAM tight
- Zero downtime deployment with instant rollback

## Critical Files to Modify

1. **requirements.txt** - Add Kokoro library dependency
2. **config.py** - Add TTS_PROVIDER and Kokoro-specific settings
3. **.env.example** - Document new environment variables
4. **src/services/tts_service.py** - Refactor to support multiple TTS providers
5. **Dockerfile** - Add Kokoro model download and setup
6. **docker-compose.yml** (optional) - Add default TTS_PROVIDER

## Implementation Steps

### 1. Update requirements.txt

Add Kokoro dependencies:
```
kokoro-onnx>=0.2.0  # Or use direct onnxruntime if preferred
# OR for minimal install:
# onnxruntime>=1.16.0
# numpy>=1.24.0
```

### 2. Update .env.example

Add new environment variables:
```bash
# TTS Provider Configuration
TTS_PROVIDER=kokoro  # Options: "piper" | "kokoro" (default: kokoro)

# Kokoro TTS Settings (when TTS_PROVIDER=kokoro)
KOKORO_MODEL_PATH=/usr/local/share/kokoro/kokoro-v0_19.onnx
KOKORO_VOICES_PATH=/usr/local/share/kokoro/voices
KOKORO_SPEAKER=af_bella  # Options: af_bella, af_sarah, am_adam, am_michael, bf_emma, bf_isabella, bm_george, bm_lewis
KOKORO_THREADS=2  # Thread count for inference

# Existing Piper settings remain unchanged
PIPER_BIN_PATH=/usr/local/bin/piper
PIPER_MODEL_PATH=/usr/local/share/piper/en_US-lessac-medium.onnx
PIPER_THREADS=2
```

### 3. Update config.py

Add TTS provider settings to Settings class:
```python
class Settings(BaseSettings):
    # ... existing settings ...

    # TTS Provider Selection
    TTS_PROVIDER: str = Field(default="kokoro", description="TTS provider: piper or kokoro")

    # Kokoro TTS Configuration
    KOKORO_MODEL_PATH: str = Field(
        default="/usr/local/share/kokoro/kokoro-v0_19.onnx",
        description="Path to Kokoro ONNX model"
    )
    KOKORO_VOICES_PATH: str = Field(
        default="/usr/local/share/kokoro/voices",
        description="Path to Kokoro voice files directory"
    )
    KOKORO_SPEAKER: str = Field(
        default="af_bella",
        description="Kokoro speaker voice name"
    )
    KOKORO_THREADS: int = Field(
        default=2,
        description="Thread count for Kokoro inference"
    )

    # Existing Piper settings remain unchanged
    PIPER_BIN_PATH: str = Field(...)
    PIPER_MODEL_PATH: str = Field(...)
    PIPER_THREADS: int = Field(...)
```

### 4. Refactor src/services/tts_service.py

**Architecture changes:**

Current structure:
```
generate_audio(sentences: List[str], job_dir: Path) -> Path
  └─> Directly calls Piper subprocess
```

New structure:
```
generate_audio(sentences: List[str], job_dir: Path) -> Path
  └─> get_tts_provider() -> TTSProvider (interface)
       ├─> PiperTTSProvider.generate_audio()
       └─> KokoroTTSProvider.generate_audio()
```

**Implementation approach:**

1. Create TTSProvider abstract base class with interface:
   - `generate_audio(sentences: List[str], job_dir: Path) -> Path`
   - Returns path to final concatenated voice.wav

2. Refactor existing Piper code into PiperTTSProvider class
   - Move all Piper-specific logic into this class
   - Keep existing behavior unchanged

3. Create new KokoroTTSProvider class
   - Implement same interface as Piper
   - Use kokoro-onnx library or direct ONNX runtime
   - Generate per-sentence WAVs (for subtitle timing)
   - Concatenate to voice.wav using FFmpeg

4. Add factory function `get_tts_provider() -> TTSProvider`
   - Read settings.TTS_PROVIDER
   - Return appropriate provider instance
   - Raise error if provider not found

5. Update main generate_audio() function:
   - Call `provider = get_tts_provider()`
   - Call `return provider.generate_audio(sentences, job_dir)`
   - Keep all existing error handling

**Key requirements:**
- Both providers must generate per-sentence WAV files (sentence_001.wav, etc.)
- Both must concatenate to final voice.wav
- Both must handle single-sentence scripts correctly (copy, not rename)
- Both must use same file naming conventions
- Both must respect thread limits for CPU optimization

### 5. Update Dockerfile

Add Kokoro model download and setup:

```dockerfile
# ... existing base image and dependencies ...

# Download Kokoro model (~350MB)
RUN mkdir -p /usr/local/share/kokoro/voices && \
    wget -O /usr/local/share/kokoro/kokoro-v0_19.onnx \
    https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/kokoro-v0_19.onnx && \
    wget -O /usr/local/share/kokoro/voices/voices.json \
    https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/voices.json

# Existing Piper setup remains unchanged
RUN wget -O /usr/local/bin/piper ... && \
    chmod +x /usr/local/bin/piper && \
    wget -O /usr/local/share/piper/en_US-lessac-medium.onnx ...

# Verify both models exist at startup
RUN test -f /usr/local/share/kokoro/kokoro-v0_19.onnx && \
    test -f /usr/local/share/piper/en_US-lessac-medium.onnx
```

**Alternative: Model volume mount (for easier updates)**
```dockerfile
# In Dockerfile: Create directories only
RUN mkdir -p /usr/local/share/kokoro /usr/local/share/piper

# In docker-compose.yml: Mount models as volume
volumes:
  - ./models/kokoro:/usr/local/share/kokoro:ro
  - ./models/piper:/usr/local/share/piper:ro
```

### 6. Local Testing

**Test both providers:**
```bash
# Test with Kokoro (new default)
TTS_PROVIDER=kokoro docker compose up

# Test with Piper (fallback)
TTS_PROVIDER=piper docker compose up
```

**Generate test audio samples:**
```bash
# Using /voiceover endpoint
curl -X POST http://localhost:8000/voiceover \
  -H "Content-Type: application/json" \
  -d '{"script": "This is a test of the Kokoro text to speech system. It should sound much more natural than Piper."}'

# Compare with Piper
TTS_PROVIDER=piper
curl -X POST http://localhost:8000/voiceover \
  -H "Content-Type: application/json" \
  -d '{"script": "This is a test of the Kokoro text to speech system. It should sound much more natural than Piper."}'
```

**Monitor resource usage:**
```bash
# Watch RAM usage in real-time
docker stats --no-stream

# Check container logs for errors
docker compose logs -f
```

## Testing & Validation

### Voice Quality Testing

**Generate test samples with both providers:**
- Short script (1 sentence)
- Medium script (10 sentences)
- Long script (50+ sentences)
- Edge cases: numbers, punctuation, special characters

**Compare audio on:**
- Naturalness of speech
- Clarity and articulation
- Emotional expression
- Consistent pacing

**Acceptance criteria:**
- Kokoro voice clearly more natural than Piper
- No audio artifacts or glitches
- Consistent quality across all script lengths

### Performance & Resource Testing

**RAM stress testing:**
```bash
# Simulate 512MB droplet
docker run -m 512m --memory-swap 2g your-image

# Simulate 1GB droplet
docker run -m 1g --memory-swap 2g your-image

# Test concurrent jobs
# Submit 3 jobs simultaneously and monitor RAM
```

**Benchmark metrics to collect:**
- Peak RAM usage per job
- Processing time per sentence (Kokoro vs Piper)
- Total job completion time
- Swap usage under load
- Check for OOM kills: `dmesg | grep -i "out of memory"`

**Acceptance criteria:**
- No OOM crashes with 2 concurrent jobs minimum
- Processing time ≤2× Piper baseline (acceptable for quality gain)
- Swap usage stays <500MB under peak load

### Integration Testing

**Test all endpoints:**
1. `POST /voiceover` - TTS generation + S3 upload
2. `POST /render` - Full pipeline (script → TTS → subtitles → video)
3. `POST /render-video` - Manual render with provided voiceover
4. `GET /status/{job_id}` - Job status tracking

**Verify:**
- All S3 uploads succeed with correct paths
- Subtitle timing remains accurate (ffprobe durations match)
- FFmpeg concatenation works correctly
- Webhook notifications sent successfully
- Error handling graceful (bad input, model failures)

## Production Deployment

### Rollout Strategy

**Phase 1: Single droplet deployment**
1. Deploy to 1 droplet with `TTS_PROVIDER=kokoro`
2. Monitor for 24 hours minimum
3. Track: RAM trends, job times, error rate, webhook success

**Phase 2: Full rollout (if stable)**
1. Deploy to remaining droplets
2. Continue monitoring all instances
3. Keep `TTS_PROVIDER` easily accessible for quick changes

**Rollback trigger points:**
- OOM kills occurring
- Processing time >3× slower than Piper
- Error rate >5%
- Audio quality issues reported

### Monitoring Checklist

**System metrics:**
- RAM usage (current, peak, trends)
- Swap usage patterns
- CPU utilization
- Disk space (model files + temp audio)

**Application metrics:**
- Job completion times
- Queue depth and wait times
- Error rates by endpoint
- Webhook delivery success rate
- S3 upload success rate

### Optimization (If Needed)

**If RAM is tight:**
- Reduce `MAX_CONCURRENT_JOBS` from 3 to 2
- Implement model unloading between jobs
- Tune `KOKORO_THREADS` (try 1, 2, 4)
- Consider lazy model loading

**If speed is slower:**
- Optimize thread count for CPU cores
- Pre-warm model at service startup
- Profile where time is spent (TTS vs FFmpeg vs network)
- Check if concatenation is bottleneck

## Rollback Plan

**If Kokoro doesn't work:**
1. Change `TTS_PROVIDER=piper` in .env
2. Restart service (no code changes needed)
3. Service continues with original Piper voice
4. Zero downtime, no data loss

**If RAM issues persist:**
- Keep Kokoro but reduce `MAX_CONCURRENT_JOBS` to 1-2
- Or revert to Piper permanently
- Consider upgrading to 2GB droplets (~$8/month) as middle ground

## Sources & References

- [The Best Open-Source Text-to-Speech Models in 2026](https://www.bentoml.com/blog/exploring-the-world-of-open-source-text-to-speech-models)
- [Best open source text-to-speech models and how to run them](https://northflank.com/blog/best-open-source-text-to-speech-models-and-how-to-run-them)
- [Kokoro TTS Installation Guide](https://dev.to/nodeshiftcloud/a-step-by-step-guide-to-install-kokoro-82m-locally-for-fast-and-high-quality-tts-58ed)
- [How to Run Kokoro TTS Locally](https://medium.com/@shrinath.suresh/setting-up-kokoro-tts-locally-a-complete-beginner-friendly-guide-c1eaade469ca)
- [MeloTTS CPU Optimization with OpenVINO](https://blog.openvino.ai/blog-posts/optimizing-melotts-for-aipc-deployment-with-openvino-a-lightweight-tts-solution)
- [Kokoro: High-Quality TTS on Your CPU with ONNX](https://learncodecamp.net/kokoro-tts/)
