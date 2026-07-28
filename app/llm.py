from __future__ import annotations

import base64
import json
import re
import time
from typing import Any

import httpx
from huggingface_hub import InferenceClient

from .config import Settings


class ModelBackendError(RuntimeError):
    """A model provider error that is safe to surface in the local UI."""


def parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    # Some reasoning models return their final answer after a thinking marker,
    # even when thinking was disabled in the request. Prefer that final segment
    # so duplicated JSON on both sides of </think> remains parseable.
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[-1].strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    # Nemotron occasionally closes the corrections array but omits only the
    # final wrapper brace. Repair this narrow, schema-specific truncation so all
    # correction entries survive; do not attempt broad arbitrary JSON repair.
    if text.startswith('{"corrections":[') and text.endswith("]"):
        text += "}"
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        # Hosted reasoning endpoints sometimes repeat the final JSON object.
        # Decode every complete object and select the last outer object instead
        # of spanning from the first "{" to the final "}", which creates
        # invalid "extra data" JSON for repeated answers.
        decoder = json.JSONDecoder()
        candidates: list[tuple[int, int, dict[str, Any]]] = []
        for match in re.finditer(r"\{", text):
            start = match.start()
            try:
                candidate, consumed = decoder.raw_decode(text[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                candidates.append((start + consumed, -start, candidate))
        if not candidates:
            raise ModelBackendError("The model response did not contain a JSON object.")
        _, _, value = max(candidates, key=lambda candidate: candidate[:2])
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


class NvidiaModelClient:
    """Small OpenAI-compatible client for NVIDIA's hosted NIM endpoint."""

    def __init__(self, settings: Settings):
        if settings.model_backend != "nvidia":
            raise ModelBackendError(f"Unsupported MODEL_BACKEND: {settings.model_backend}")
        if not settings.nvidia_api_key:
            raise ModelBackendError(
                "NVIDIA_API_KEY is missing. Add it to .env before using NVIDIA vision."
            )
        self._api_key = settings.nvidia_api_key
        self._url = settings.nvidia_api_base_url.rstrip("/") + "/chat/completions"
        self._reasoning_budget = settings.nvidia_reasoning_budget

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
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "max_tokens": max_tokens,
            "reasoning_budget": min(self._reasoning_budget, max_tokens),
            "stream": False,
            "temperature": 0.1,
            "top_p": 1,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            response = httpx.post(
                self._url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )
            if response.status_code == 401:
                raise ModelBackendError("NVIDIA rejected the API key.")
            if response.status_code == 402:
                raise ModelBackendError("NVIDIA hosted inference credits are unavailable.")
            if response.status_code == 403:
                raise ModelBackendError("The NVIDIA API key cannot invoke this model.")
            if response.status_code == 429:
                raise ModelBackendError(
                    "NVIDIA's development endpoint is rate-limited; try again later."
                )
            response.raise_for_status()
            body = response.json()
            raw = body.get("choices", [{}])[0].get("message", {}).get("content")
            if not isinstance(raw, str):
                raise ModelBackendError("The NVIDIA model returned an empty response.")
            return parse_json_object(raw)
        except ModelBackendError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            raise ModelBackendError(f"NVIDIA inference failed: {detail}") from exc


class GroqModelClient:
    """OpenAI-compatible vision client for Groq-hosted Qwen models."""

    def __init__(self, settings: Settings):
        if settings.model_backend != "groq":
            raise ModelBackendError(f"Unsupported MODEL_BACKEND: {settings.model_backend}")
        if not settings.groq_api_key:
            raise ModelBackendError(
                "GROQ_API_KEY is missing. Add it to .env before using Groq vision."
            )
        self._api_key = settings.groq_api_key
        self._url = settings.groq_api_base_url.rstrip("/") + "/chat/completions"

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
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_object"},
            "reasoning_effort": "none",
            "max_tokens": max_tokens,
            "stream": False,
            "temperature": 0.1,
            "top_p": 1,
        }
        try:
            for attempt in range(2):
                response = httpx.post(
                    self._url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=120,
                )
                if response.status_code not in {502, 503, 504} or attempt == 1:
                    break
                time.sleep(1)
            if response.status_code == 401:
                raise ModelBackendError("Groq rejected the API key.")
            if response.status_code == 403:
                raise ModelBackendError("The Groq API key cannot invoke this model.")
            if response.status_code == 429:
                raise ModelBackendError(
                    "Groq's free endpoint is rate-limited; try again after its retry window."
                )
            response.raise_for_status()
            body = response.json()
            raw = body.get("choices", [{}])[0].get("message", {}).get("content")
            if not isinstance(raw, str):
                raise ModelBackendError("The Groq model returned an empty response.")
            return parse_json_object(raw)
        except ModelBackendError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            raise ModelBackendError(f"Groq inference failed: {detail}") from exc
