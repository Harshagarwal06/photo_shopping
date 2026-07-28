import httpx
import pytest

from app.config import Settings
from app.llm import GroqModelClient, ModelBackendError, NvidiaModelClient, parse_json_object


def test_parse_json_object_accepts_fenced_json():
    assert parse_json_object('```json\n{"items": []}\n```') == {"items": []}


def test_parse_json_object_extracts_object_from_short_preamble():
    assert parse_json_object('Result: {"product_id": "x"}') == {"product_id": "x"}


def test_parse_json_object_prefers_answer_after_reasoning_marker():
    raw = '{"wrong": true}</think>\\n```json\\n{"correct": true}\\n```'
    assert parse_json_object(raw) == {"correct": True}


def test_parse_json_object_accepts_repeated_final_objects_without_marker():
    raw = '{"draft": true}\\n{"final": true}'
    assert parse_json_object(raw) == {"final": True}


def test_parse_json_object_repairs_missing_corrections_wrapper_brace():
    raw = '{"corrections":[{"id":"line-1","search_term":"Maggi"}]'
    assert parse_json_object(raw) == {
        "corrections": [{"id": "line-1", "search_term": "Maggi"}]
    }


def test_parse_json_object_rejects_non_json():
    with pytest.raises(ModelBackendError):
        parse_json_object("no object here")


def test_nvidia_client_uses_openai_compatible_json_response(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"draft": "thinking"}</think>{"items": []}'
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr("app.llm.httpx.post", fake_post)
    settings = Settings(
        _env_file=None,
        model_backend="nvidia",
        nvidia_api_key="nvapi-test",
        nvidia_api_base_url="https://example.test/v1",
    )

    result = NvidiaModelClient(settings).complete_json(
        model="nvidia/test-model",
        system="Return JSON.",
        prompt="Make a plan.",
    )

    assert result == {"items": []}
    assert captured["url"] == "https://example.test/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer nvapi-test"
    assert captured["json"]["stream"] is False
    assert captured["json"]["chat_template_kwargs"]["enable_thinking"] is False


def test_groq_client_uses_vision_json_mode_without_reasoning(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"choices": [{"message": {"content": '{"items": []}'}}]},
        )

    monkeypatch.setattr("app.llm.httpx.post", fake_post)
    settings = Settings(
        _env_file=None,
        model_backend="groq",
        groq_api_key="gsk-test",
        groq_api_base_url="https://example.test/openai/v1",
    )

    result = GroqModelClient(settings).complete_json(
        model="qwen/test",
        system="Return JSON.",
        prompt="Read this.",
        image_bytes=b"image",
    )

    assert result == {"items": []}
    assert captured["url"] == "https://example.test/openai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer gsk-test"
    assert captured["json"]["model"] == "qwen/test"
    assert captured["json"]["reasoning_effort"] == "none"
    assert captured["json"]["response_format"] == {"type": "json_object"}
    content = captured["json"]["messages"][1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_groq_client_retries_one_transient_service_failure(monkeypatch):
    responses = iter(
        [
            httpx.Response(
                503,
                request=httpx.Request("POST", "https://example.test/chat/completions"),
            ),
            httpx.Response(
                200,
                request=httpx.Request("POST", "https://example.test/chat/completions"),
                json={"choices": [{"message": {"content": '{"items": []}'}}]},
            ),
        ]
    )
    calls = []
    monkeypatch.setattr(
        "app.llm.httpx.post",
        lambda *_args, **_kwargs: calls.append(True) or next(responses),
    )
    monkeypatch.setattr("app.llm.time.sleep", lambda _seconds: None)
    settings = Settings(
        _env_file=None,
        model_backend="groq",
        groq_api_key="gsk-test",
    )

    result = GroqModelClient(settings).complete_json(
        model="qwen/test",
        system="Return JSON.",
        prompt="Read this.",
    )

    assert result == {"items": []}
    assert len(calls) == 2


def test_cloud_backend_prefers_groq_but_respects_explicit_hf_choice():
    automatic = Settings(
        _env_file=None,
        hf_token="hf-test",
        nvidia_api_key="nvapi-test",
        groq_api_key="gsk-test",
        cloud_model_backend="auto",
    )
    explicit_hf = automatic.model_copy(update={"cloud_model_backend": "hf"})

    assert automatic.cloud_backend == "groq"
    assert automatic.cloud_model == automatic.groq_model_id
    assert explicit_hf.cloud_backend == "hf"
