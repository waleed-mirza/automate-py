import logging
import json
import httpx
from config import settings

logger = logging.getLogger(__name__)

class PromptEnhancementService:
    """Service to enhance script sentences into detailed image generation prompts using OpenAI GPT-4."""

    async def enhance_prompts(self, sentences: list[str]) -> list[str]:
        """
        Convert a list of script sentences into detailed image generation prompts using OpenAI GPT-4.
        
        Args:
            sentences: List of text sentences from the script.
            
        Returns:
            List of enhanced image prompts (same length as input).
        """
        if not sentences:
            return []
            
        if not settings.openai_api_key:
            logger.error("OpenAI API key not configured")
            raise ValueError("OpenAI API key not configured")
            
        # System prompt to guide GPT-4
        system_prompt = (
            "You are an expert visual prompt engineer for AI image generation (Flux-1-schnell model). "
            "Your task is to convert script sentences into vivid, detailed visual descriptions. "
            "Guidelines:\n"
            "1. Focus on composition, lighting, mood, and colors.\n"
            "2. Keep prompts concise but detailed (2-3 sentences max).\n"
            "3. NO text in the images.\n"
            "4. Return ONLY a valid JSON array of strings, where each string corresponds to the input sentence in order."
        )
        
        # Prepare the user content
        user_content = json.dumps(sentences)
        
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "gpt-4",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Enhance these sentences: {user_content}"}
            ],
            "temperature": 0.7
        }
        
        content = ""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers=headers,
                    json=payload
                )
                
                if response.status_code != 200:
                    logger.error(f"OpenAI API failed: {response.status_code} - {response.text}")
                    raise RuntimeError(f"OpenAI API failed: {response.status_code}")
                
                data = response.json()
                content = data["choices"][0]["message"]["content"].strip()
                
                # Clean up content if it contains markdown code blocks
                if content.startswith("```json"):
                    content = content.replace("```json", "", 1)
                if content.startswith("```"):
                    content = content.replace("```", "", 1)
                if content.endswith("```"):
                    content = content.rsplit("```", 1)[0]
                
                content = content.strip()
                
                # Extract JSON array - find first '[' and last ']'
                start_idx = content.find('[')
                end_idx = content.rfind(']')
                
                if start_idx == -1 or end_idx == -1 or start_idx >= end_idx:
                    logger.error(f"No valid JSON array found in response. Content: {content[:500]}")
                    raise ValueError("OpenAI response does not contain a valid JSON array")
                
                json_content = content[start_idx:end_idx + 1]
                enhanced_prompts = json.loads(json_content)
                
                if not isinstance(enhanced_prompts, list):
                    raise ValueError("OpenAI returned non-list format")
                    
                if len(enhanced_prompts) != len(sentences):
                    logger.warning(
                        f"Mismatch in enhanced prompts count. Expected {len(sentences)}, got {len(enhanced_prompts)}. "
                        "Padding or truncating."
                    )
                    # Adjust length to match input to prevent index errors downstream
                    if len(enhanced_prompts) < len(sentences):
                        enhanced_prompts.extend(sentences[len(enhanced_prompts):])
                    else:
                        enhanced_prompts = enhanced_prompts[:len(sentences)]
                
                return enhanced_prompts

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse OpenAI JSON response: {e}. Content: {content[:1000] if content else 'N/A'}")
            raise
        except Exception as e:
            logger.error(f"Error in enhance_prompts: {e}")
            raise

# Singleton instance
prompt_enhancement_service = PromptEnhancementService()
