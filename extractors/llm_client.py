"""
Local LLM Client for Real Estate Document Extractor.

Communicates with Ollama running locally. All inference happens
on-device — no data is transmitted to any external service.
"""

import json
import logging
import requests
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1:8b"  # Good balance of capability and speed


class LocalLLMClient:
    """Client for local LLM inference via Ollama."""

    def __init__(self, base_url: str = DEFAULT_OLLAMA_URL,
                 model: str = DEFAULT_MODEL,
                 temperature: float = 0.1,
                 max_tokens: int = 4096):
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def is_available(self) -> bool:
        """Check if Ollama is running and the model is available."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = resp.json().get('models', [])
                model_names = [m['name'] for m in models]
                if self.model in model_names:
                    return True
                # Check without tag
                base_model = self.model.split(':')[0]
                return any(base_model in name for name in model_names)
            return False
        except Exception:
            return False

    def list_models(self) -> List[str]:
        """List available models in Ollama."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if resp.status_code == 200:
                return [m['name'] for m in resp.json().get('models', [])]
        except Exception:
            pass
        return []

    def generate(self, prompt: str, system_prompt: str = "",
                 format_json: bool = True) -> Optional[str]:
        """
        Generate a response from the local LLM.

        Args:
            prompt: The user/extraction prompt
            system_prompt: System-level instructions
            format_json: If True, request JSON output format

        Returns:
            The LLM's response text, or None on failure
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            }
        }

        if system_prompt:
            payload["system"] = system_prompt

        if format_json:
            payload["format"] = "json"

        try:
            logger.info(f"Sending request to local LLM ({self.model})...")
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=300  # 5 min timeout for long documents
            )

            if resp.status_code == 200:
                result = resp.json()
                response_text = result.get('response', '')
                logger.info(
                    f"LLM response received "
                    f"({result.get('eval_count', '?')} tokens, "
                    f"{result.get('total_duration', 0) / 1e9:.1f}s)"
                )
                return response_text
            else:
                logger.error(f"LLM request failed: {resp.status_code} {resp.text}")
                return None

        except requests.exceptions.ConnectionError:
            logger.error(
                "Cannot connect to Ollama. Make sure it's running: "
                "'ollama serve' or check if it's installed: https://ollama.ai"
            )
            return None
        except requests.exceptions.Timeout:
            logger.error("LLM request timed out. The document may be too long.")
            return None
        except Exception as e:
            logger.error(f"LLM request error: {e}")
            return None

    def generate_structured(self, prompt: str, system_prompt: str = "") -> Optional[Any]:
        """
        Generate and parse a JSON response from the LLM.

        Returns parsed JSON object, or None on failure.
        """
        response = self.generate(prompt, system_prompt, format_json=True)

        if response is None:
            return None

        try:
            return json.loads(response)
        except json.JSONDecodeError:
            # Try to extract JSON from the response
            logger.warning("Response was not valid JSON. Attempting to extract...")
            return self._extract_json(response)

    def _extract_json(self, text: str) -> Optional[Any]:
        """Attempt to extract JSON from a text response that may contain extra content."""
        # Try to find JSON array or object
        for start_char, end_char in [('[', ']'), ('{', '}')]:
            start = text.find(start_char)
            if start == -1:
                continue

            # Find matching end
            depth = 0
            for i in range(start, len(text)):
                if text[i] == start_char:
                    depth += 1
                elif text[i] == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i+1])
                        except json.JSONDecodeError:
                            break

        logger.error("Could not extract valid JSON from LLM response")
        return None

    def chunk_text(self, text: str, max_chars: int = 6000,
                   overlap: int = 500) -> List[str]:
        """
        Split long text into overlapping chunks for processing.
        Tries to break at paragraph boundaries.
        """
        if len(text) <= max_chars:
            return [text]

        chunks = []
        start = 0

        while start < len(text):
            end = start + max_chars

            if end < len(text):
                # Try to break at a paragraph boundary
                break_point = text.rfind('\n\n', start + max_chars // 2, end)
                if break_point == -1:
                    break_point = text.rfind('\n', start + max_chars // 2, end)
                if break_point == -1:
                    break_point = text.rfind('. ', start + max_chars // 2, end)
                if break_point != -1:
                    end = break_point + 1

            chunks.append(text[start:end])
            start = end - overlap  # overlap for context continuity

        return chunks
