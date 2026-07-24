from __future__ import annotations

import base64
import json
import re
from typing import Any

from huggingface_hub import InferenceClient

from .config import Settings


class ModelBackendError(RuntimeError):
    """A model provider error that is safe to surface in the local UI."""


def parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ModelBackendError("The model response did not contain a JSON object.")
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ModelBackendError(f"The model returned invalid JSON: {exc.msg}.") from exc
    if not isinstance(value, dict):
        raise ModelBackendError("The model response must be a JSON object.")
    return value


class HFModelClient:
    def __init__(self, settings: Settings):
        if settings.model_backend != "hf":
            raise ModelBackendError(f"Unsupported MODEL_BACKEND: {settings.model_backend}")
        if not settings.hf_token:
            raise ModelBackendError(
                "HF_TOKEN is missing. Add it to .env before planning a cart."
            )
        provider = None if settings.hf_provider == "auto" else settings.hf_provider
        self._client = InferenceClient(token=settings.hf_token, provider=provider)

    def complete_json(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        image_bytes: bytes | None = None,
        image_media_type: str = "image/jpeg",
        max_tokens: int = 1800,
    ) -> dict[str, Any]:
        content: str | list[dict[str, Any]] = prompt
        if image_bytes:
            encoded = base64.b64encode(image_bytes).decode("ascii")
            content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{image_media_type};base64,{encoded}"},
                },
            ]
        try:
            response = self._client.chat_completion(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=max_tokens,
            )
            raw = response.choices[0].message.content
            if not isinstance(raw, str):
                raise ModelBackendError("The model returned an empty response.")
            return parse_json_object(raw)
        except ModelBackendError:
            raise
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            raise ModelBackendError(f"Hugging Face inference failed: {detail}") from exc

