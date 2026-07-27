import httpx
import pytest

from app.config import Settings
from app.llm import ModelBackendError, NvidiaModelClient, parse_json_object


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


def test_cloud_backend_prefers_nvidia_but_respects_explicit_hf_choice():
    automatic = Settings(
        _env_file=None,
        hf_token="hf-test",
        nvidia_api_key="nvapi-test",
        cloud_model_backend="auto",
    )
    explicit_hf = automatic.model_copy(update={"cloud_model_backend": "hf"})

    assert automatic.cloud_backend == "nvidia"
    assert automatic.cloud_model == automatic.nvidia_model_id
    assert explicit_hf.cloud_backend == "hf"
