from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.app.agent.hypotheses import EvidenceReference, HypothesisResult
from backend.app.models.groq_provider import DEFAULT_GROQ_MODEL, GroqModelProvider
from backend.app.models.provider import ModelProvider

SYSTEM_PROMPT = "Investigate using only supplied evidence."
USER_PROMPT = "checkout-api p95 latency 1940 ms, error rate 8.2%."

SAMPLE_PAYLOAD = {
    "hypotheses": [
        {
            "cause": "db_connection_pool_regression",
            "confidence": 0.91,
            "evidence": [
                {
                    "source_type": "deployment",
                    "summary": "deployment v1.18.3 occurred shortly before incident",
                },
                {
                    "source_type": "log",
                    "summary": "logs contain database connection pool timeout errors",
                },
            ],
        }
    ],
    "recommended_next_action": "rollback_deployment",
    "reasoning_summary": (
        "Evidence strongly correlates the recent deployment with "
        "database pool failures and checkout degradation."
    ),
}


def _completion_response(content: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _provider_with_mock(
    content: str | None = json.dumps(SAMPLE_PAYLOAD),
) -> tuple[GroqModelProvider, MagicMock]:
    client = MagicMock()
    client.chat.completions.create.return_value = _completion_response(content)
    provider = GroqModelProvider(api_key="test-key", client=client)
    return provider, client


def _create_kwargs(client: MagicMock) -> dict:
    return client.chat.completions.create.call_args.kwargs


def test_groq_provider_implements_model_provider() -> None:
    provider, _ = _provider_with_mock()

    assert isinstance(provider, ModelProvider)


def test_default_model_is_gpt_oss_20b() -> None:
    provider, _ = _provider_with_mock()

    assert provider.model == "openai/gpt-oss-20b"
    assert DEFAULT_GROQ_MODEL == "openai/gpt-oss-20b"


def test_explicit_api_key_initializes_provider() -> None:
    fake_client = MagicMock()
    with patch(
        "backend.app.models.groq_provider.Groq", return_value=fake_client
    ) as groq_cls:
        provider = GroqModelProvider(api_key="test-key")

    groq_cls.assert_called_once_with(api_key="test-key")
    assert provider.model == DEFAULT_GROQ_MODEL


def test_missing_api_key_raises_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        GroqModelProvider()


def test_generate_structured_sends_system_prompt() -> None:
    provider, client = _provider_with_mock()

    provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)

    messages = _create_kwargs(client)["messages"]
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}


def test_generate_structured_sends_user_prompt() -> None:
    provider, client = _provider_with_mock()

    provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)

    messages = _create_kwargs(client)["messages"]
    assert messages[1] == {"role": "user", "content": USER_PROMPT}


def test_request_uses_configured_model() -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = _completion_response(
        json.dumps(SAMPLE_PAYLOAD)
    )
    provider = GroqModelProvider(
        api_key="test-key",
        model="openai/gpt-oss-20b",
        client=client,
    )

    provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)

    assert _create_kwargs(client)["model"] == "openai/gpt-oss-20b"


def test_request_uses_json_schema_response_format() -> None:
    provider, client = _provider_with_mock()

    provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)

    assert _create_kwargs(client)["response_format"]["type"] == "json_schema"


def test_request_sets_strict_true() -> None:
    provider, client = _provider_with_mock()

    provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)

    json_schema = _create_kwargs(client)["response_format"]["json_schema"]
    assert json_schema["strict"] is True


def test_generated_schema_matches_response_model() -> None:
    provider, client = _provider_with_mock()

    provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)

    json_schema = _create_kwargs(client)["response_format"]["json_schema"]
    assert json_schema["name"] == "HypothesisResult"
    assert json_schema["schema"] == HypothesisResult.model_json_schema()
    assert json_schema["schema"]["additionalProperties"] is False
    assert set(json_schema["schema"]["required"]) == set(
        json_schema["schema"]["properties"]
    )


def test_returned_json_validates_as_hypothesis_result() -> None:
    provider, _ = _provider_with_mock()

    result = provider.generate_structured(
        SYSTEM_PROMPT, USER_PROMPT, HypothesisResult
    )

    assert isinstance(result, HypothesisResult)
    assert result.recommended_next_action == "rollback_deployment"
    assert result.hypotheses[0].cause == "db_connection_pool_regression"


def test_nested_evidence_references_validate() -> None:
    provider, _ = _provider_with_mock()

    result = provider.generate_structured(
        SYSTEM_PROMPT, USER_PROMPT, HypothesisResult
    )

    evidence = result.hypotheses[0].evidence
    assert evidence
    assert all(isinstance(item, EvidenceReference) for item in evidence)
    assert evidence[1].source_type == "log"
    assert "database connection pool timeout" in evidence[1].summary


def test_empty_response_content_raises_runtime_error() -> None:
    provider, _ = _provider_with_mock(content=None)

    with pytest.raises(RuntimeError, match="empty structured output"):
        provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)


def test_no_real_network_request() -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = _completion_response(
        json.dumps(SAMPLE_PAYLOAD)
    )
    with patch("backend.app.models.groq_provider.Groq") as groq_cls:
        provider = GroqModelProvider(api_key="test-key", client=client)
        provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)

    groq_cls.assert_not_called()
    client.chat.completions.create.assert_called_once()
