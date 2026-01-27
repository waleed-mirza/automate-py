import logging
import httpx
import base64
import asyncio
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

from config import settings

logger = logging.getLogger(__name__)

class AIThumbnailService:
    """Generate eye-catching thumbnails using Cloudflare Workers AI + Pillow text overlay."""
    
    MODEL_ID = "@cf/black-forest-labs/flux-1-schnell"
    
    ASPECT_DIMENSIONS = {
        "16:9": {"gen": (1024, 576), "final": (1280, 720)},
        "9:16": {"gen": (576, 1024), "final": (720, 1280)},
        "1:1": {"gen": (1024, 1024), "final": (1080, 1080)},
    }
    
    async def generate_thumbnail(
        self,
        script: str,
        job_dir: Path,
        aspect_ratio: str = "16:9",
        title: str | None = None
    ) -> Path:
        """Generate AI thumbnail with text overlay."""
        # 1. Extract hook text from script or use title
        hook_text = self._extract_hook(script, title)
        
        # 2. Build prompt for background generation
        prompt = self._build_prompt(script)
        
        # 3. Get dimensions for aspect ratio
        dims = self.ASPECT_DIMENSIONS.get(aspect_ratio, self.ASPECT_DIMENSIONS["16:9"])
        
        # 4. Generate background via Cloudflare AI
        try:
            bg_image = await self._generate_background(prompt, dims["gen"])
        except Exception as e:
            logger.error(f"Failed to generate AI thumbnail background: {e}")
            # Fallback or re-raise? 
            # Given this is a service, maybe we should raise to let caller handle or fallback to default thumbnail service
            raise
        
        # 5. Resize to final dimensions
        bg_image = bg_image.resize(dims["final"], Image.Resampling.LANCZOS)
        
        # 6. Add text overlay
        final_image = self._add_text_overlay(bg_image, hook_text)
        
        # 7. Save and return
        output_path = job_dir / "thumbnail.jpg"
        final_image.convert("RGB").save(output_path, "JPEG", quality=95)
        
        return output_path

    async def generate_images_batch(
        self,
        prompts: list[str],
        job_dir: Path,
        aspect_ratio: str = "16:9"
    ) -> list[Path]:
        """Generate multiple images from prompts in parallel.
        
        Args:
            prompts: List of image generation prompts
            job_dir: Directory to save images
            aspect_ratio: Aspect ratio for images
            
        Returns:
            List of paths to generated images (image_0.jpg, image_1.jpg, etc.)
        """
        # 1. Get dimensions for aspect ratio
        dims = self.ASPECT_DIMENSIONS.get(aspect_ratio, self.ASPECT_DIMENSIONS["16:9"])
        
        async def generate_one(index: int, prompt: str) -> Path | None:
            max_retries = 2
            for attempt in range(max_retries):
                try:
                    output_path = job_dir / f"image_{index}.jpg"
                    if output_path.exists() and output_path.stat().st_size > 0:
                        logger.debug(f"Using cached image {output_path.name}")
                        return output_path

                    # 3. Call _generate_background
                    bg_image = await self._generate_background(prompt, dims["gen"])
                    
                    # 4. Resize to final dimensions
                    bg_image = bg_image.resize(dims["final"], Image.Resampling.LANCZOS)
                    
                    # 5. Save as image_X.jpg
                    bg_image.convert("RGB").save(output_path, "JPEG", quality=95)
                    return output_path
                except Exception as e:
                    # 7. Error handling with retry
                    if attempt < max_retries - 1:
                        logger.warning(f"Failed to generate image {index} (attempt {attempt + 1}/{max_retries}): {e}. Retrying...")
                        await asyncio.sleep(1)  # Brief delay before retry
                    else:
                        logger.error(f"Failed to generate image {index} after {max_retries} attempts: {e}")
                        return None
            return None

        # 2. Use asyncio.gather
        tasks = [generate_one(i, p) for i, p in enumerate(prompts)]
        results = await asyncio.gather(*tasks)
        
        # 6. Return list of paths (filtering out failures)
        successful_paths = [path for path in results if path is not None]
        failed_count = len(results) - len(successful_paths)
        
        if failed_count > 0:
            logger.warning(
                f"Image generation batch: {len(successful_paths)}/{len(prompts)} succeeded, "
                f"{failed_count} failed"
            )
        else:
            logger.info(f"Image generation batch: All {len(prompts)} images generated successfully")
        
        return successful_paths
    
    def _extract_hook(self, script: str, title: str | None) -> str:
        """Extract 2-4 word hook from script or title."""
        if title:
            words = title.split()[:4]
            return " ".join(words).upper()
        
        # Extract first meaningful phrase from script
        # Remove common filler words or just take first few words
        # Simple implementation as requested
        words = script.split()[:4]
        return " ".join(words).upper()
    
    def _build_prompt(self, script: str) -> str:
        """Build Cloudflare AI prompt for thumbnail background."""
        # Use the full script for richer context (trimmed to a safe length)
        script_context = " ".join(script.split())[:400]
        return (
            f"Create a highly clickable YouTube thumbnail background based on: {script_context}. "
            "Center a clear focal subject, dramatic perspective, and strong visual hierarchy. "
            "Vibrant saturated colors with bold contrast, cinematic lighting, rim light, and subtle glow. "
            "Clean background with depth (foreground, midground, background) and gentle bokeh. "
            "Use rule of thirds, leave generous negative space for text overlay, no text in image."
        )
    
    async def _generate_background(self, prompt: str, dimensions: tuple[int, int]) -> Image.Image:
        """Call Cloudflare Workers AI to generate background image."""
        if not settings.cloudflare_account_id or not settings.cloudflare_api_token:
             raise ValueError("Cloudflare credentials not configured")

        url = f"https://api.cloudflare.com/client/v4/accounts/{settings.cloudflare_account_id}/ai/run/{self.MODEL_ID}"
        
        headers = {
            "Authorization": f"Bearer {settings.cloudflare_api_token}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "prompt": prompt,
            "num_steps": 4,
            "width": dimensions[0],
            "height": dimensions[1],
        }
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=payload)
            
            if response.status_code != 200:
                raise RuntimeError(f"Cloudflare AI failed: {response.status_code} - {response.text}")
            
            # Cloudflare Workers AI returns JSON with base64-encoded image
            # Response format: {"image": "base64..."} or wrapped {"result": {"image": "..."}, "success": true}
            data = response.json()
            
            # Check for API error wrapper
            if "success" in data and not data.get("success"):
                errors = data.get("errors", [])
                raise RuntimeError(f"Cloudflare AI error: {errors}")
            
            # Extract base64 image - try direct first, then wrapped format
            image_b64 = data.get("image") or data.get("result", {}).get("image")
            if not image_b64:
                raise RuntimeError(f"No image in Cloudflare response: {list(data.keys())}")
            
            image_bytes = base64.b64decode(image_b64)
            return Image.open(BytesIO(image_bytes))
    
    def _add_text_overlay(self, image: Image.Image, text: str) -> Image.Image:
        """Add bold text with outline to image, wrapping if too wide."""
        draw = ImageDraw.Draw(image)
        
        # Max width for text (80% of image width for padding)
        max_text_width = int(image.width * 0.85)
        
        # Try to load a good font, fallback to default
        font_size = int(image.width * 0.08)  # 8% of width
        try:
            # Common linux font paths
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except OSError:
            try:
                font = ImageFont.truetype("arial.ttf", font_size)
            except OSError:
                font = ImageFont.load_default()
        
        # Wrap text to fit within max width
        lines = self._wrap_text(draw, text, font, max_text_width)
        
        # Calculate total text block height
        line_heights = []
        line_widths = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_widths.append(bbox[2] - bbox[0])
            line_heights.append(bbox[3] - bbox[1])
        
        line_spacing = int(font_size * 0.2)  # 20% of font size for spacing
        total_height = sum(line_heights) + line_spacing * (len(lines) - 1)
        
        # Starting Y position (upper third, centered vertically)
        start_y = int(image.height * 0.25) - total_height // 2
        
        # Stroke width for outline
        outline_width = max(2, int(font_size / 15))
        
        # Draw each line
        current_y = start_y
        for i, line in enumerate(lines):
            # Center each line horizontally
            x = (image.width - line_widths[i]) // 2
            
            # Draw outline
            for dx in range(-outline_width, outline_width + 1):
                for dy in range(-outline_width, outline_width + 1):
                    if dx != 0 or dy != 0:
                        draw.text((x + dx, current_y + dy), line, font=font, fill="black")
            
            # Draw main text
            draw.text((x, current_y), line, font=font, fill="white")
            
            current_y += line_heights[i] + line_spacing
        
        return image
    
    def _wrap_text(self, draw: ImageDraw.Draw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
        """Wrap text to fit within max_width, breaking by words."""
        words = text.split()
        lines = []
        current_line = []
        
        for word in words:
            # Try adding word to current line
            test_line = " ".join(current_line + [word])
            bbox = draw.textbbox((0, 0), test_line, font=font)
            test_width = bbox[2] - bbox[0]
            
            if test_width <= max_width:
                current_line.append(word)
            else:
                # Current line is full, start new line
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]
        
        # Add remaining words
        if current_line:
            lines.append(" ".join(current_line))
        
        return lines if lines else [text]


# Singleton instance
ai_thumbnail_service = AIThumbnailService()
