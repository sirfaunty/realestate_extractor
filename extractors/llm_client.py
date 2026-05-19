"""
Local LLM Client for Real Estate Document Extractor.

Communicates with Ollama running locally. All inference happens
on-device — no data is transmitted to any external service.

Model Version Pinning
─────────────────────
Set CAPACTIVE_MODEL_DIGEST to the sha256 digest of the exact model
binary you want to enforce.  When set, the client will refuse to run
if the locally installed model doesn't match — preventing silent
drift from model updates or different quantisations across devices.

To find a model's digest:
    curl http://localhost:11434/api/tags | python3 -m json.tool
    # look for the "digest" field next to your model name

Or use the helper:
    python -m realestate_extractor.extractors.llm_client --pin
"""

import json
import logging
import os
import requests
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1:8b"  # Good balance of capability and speed


class ModelVersionMismatch(RuntimeError):
    """Raised when the running model digest doesn't match the pinned digest."""
    pass


class LocalLLMClient:
    """Client for local LLM inference via Ollama."""

    def __init__(self, base_url: str = DEFAULT_OLLAMA_URL,
                 model: str = DEFAULT_MODEL,
                 temperature: float = 0.1,
                 max_tokens: int = 4096,
                 pinned_digest: Optional[str] = None):
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        # Digest pin: explicit arg > env var > None (no enforcement)
        self.pinned_digest = (
            pinned_digest
            or os.environ.get('CAPACTIVE_MODEL_DIGEST')
            or None
        )
        self._digest_verified = False

    # ─── Model Version Pinning ──────────────────────────────────────

    def get_model_info(self) -> Optional[Dict[str, Any]]:
        """Return full model metadata from Ollama /api/show."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/show",
                json={"name": self.model},
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def get_running_digest(self) -> Optional[str]:
        """Get the digest of the currently installed model."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if resp.status_code == 200:
                for m in resp.json().get('models', []):
                    if m['name'] == self.model or self.model.split(':')[0] in m['name']:
                        return m.get('digest')
        except Exception:
            pass
        return None

    def verify_digest(self, raise_on_mismatch: bool = True) -> Dict[str, Any]:
        """
        Compare the running model's digest against the pinned digest.

        Returns a dict with:
          - pinned: the expected digest (or None)
          - running: the actual digest found
          - match: True/False/None (None when no pin is set)

        Raises ModelVersionMismatch if raise_on_mismatch=True and
        the digests don't match.
        """
        running = self.get_running_digest()
        result = {
            "pinned": self.pinned_digest,
            "running": running,
            "model": self.model,
        }

        if not self.pinned_digest:
            result["match"] = None  # no pin configured
            return result

        # Compare using prefix match (Ollama sometimes truncates)
        if running and self.pinned_digest:
            min_len = min(len(self.pinned_digest), len(running))
            matches = running[:min_len] == self.pinned_digest[:min_len]
        else:
            matches = False

        result["match"] = matches

        if not matches and raise_on_mismatch:
            msg = (
                f"Model version mismatch!\n"
                f"  Expected digest: {self.pinned_digest}\n"
                f"  Running digest:  {running or '(model not found)'}\n"
                f"  Model:           {self.model}\n\n"
                f"This means the model binary has changed since it was pinned.\n"
                f"To accept the new version, update CAPACTIVE_MODEL_DIGEST.\n"
                f"To revert, run: ollama pull {self.model}@{self.pinned_digest}"
            )
            raise ModelVersionMismatch(msg)

        return result

    def _ensure_digest(self):
        """One-time digest check on first use (lazy, not in __init__)."""
        if self._digest_verified or not self.pinned_digest:
            return
        self.verify_digest(raise_on_mismatch=True)
        self._digest_verified = True

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
            # Enforce digest pin on first generation call
            self._ensure_digest()

            prompt_len = len(prompt)
            logger.info(
                f"Sending request to local LLM ({self.model}) — "
                f"{prompt_len:,} chars prompt..."
            )
            import time as _time
            _start = _time.time()
            # Timeout scales with prompt length:
            #   ≤3K chars → 30s (gap-fill calls with truncated text)
            #   ≤10K chars → 60s
            #   >10K chars → 90s (full doc extraction — rare after skip guards)
            _timeout = 30 if prompt_len <= 3000 else (60 if prompt_len <= 10000 else 90)
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=_timeout,
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


# ─── CLI Helper ─────────────────────────────────────────────────────
#
#   python -m realestate_extractor.extractors.llm_client --pin
#   python -m realestate_extractor.extractors.llm_client --verify
#
if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Ollama model version pinning helper")
    parser.add_argument("--url", default=DEFAULT_OLLAMA_URL, help="Ollama API URL")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model name")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pin", action="store_true",
                       help="Print the current model digest (use this value for CAPACTIVE_MODEL_DIGEST)")
    group.add_argument("--verify", action="store_true",
                       help="Verify the running model against CAPACTIVE_MODEL_DIGEST")
    group.add_argument("--info", action="store_true",
                       help="Print full model metadata")
    args = parser.parse_args()

    client = LocalLLMClient(base_url=args.url, model=args.model)

    if args.pin:
        digest = client.get_running_digest()
        if digest:
            print(f"Model:  {args.model}")
            print(f"Digest: {digest}")
            print()
            print("To pin this version, set the environment variable:")
            print(f"  export CAPACTIVE_MODEL_DIGEST={digest}")
        else:
            print(f"Could not find model '{args.model}'. Is Ollama running?", file=sys.stderr)
            sys.exit(1)

    elif args.verify:
        pinned = os.environ.get('CAPACTIVE_MODEL_DIGEST')
        if not pinned:
            print("CAPACTIVE_MODEL_DIGEST is not set. Run with --pin first.", file=sys.stderr)
            sys.exit(1)
        client.pinned_digest = pinned
        try:
            result = client.verify_digest(raise_on_mismatch=True)
            print(f"OK — model digest matches.")
            print(f"  Model:  {result['model']}")
            print(f"  Digest: {result['running']}")
        except ModelVersionMismatch as e:
            print(f"MISMATCH\n{e}", file=sys.stderr)
            sys.exit(1)

    elif args.info:
        info = client.get_model_info()
        if info:
            print(json.dumps(info, indent=2))
        else:
            print(f"Could not get info for '{args.model}'.", file=sys.stderr)
            sys.exit(1)
