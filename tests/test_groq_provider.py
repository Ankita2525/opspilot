from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from backend.app.agent.hypotheses import EvidenceReference, HypothesisResult
from backend.app.models.groq_provider import (
    DEFAULT_GROQ_MODEL,
    GroqModelProvider,
    groq_strict_schema,
)
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
    "recommended_action": "rollback_deployment",
    "recommendation_summary": (
        "Connection pool exhaustion followed deployment v1.18.3. "
        "Rolling back the deployment is the safest currently available remediation."
    ),
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
    assert result.recommended_action == "rollback_deployment"
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


class ProbeResult(BaseModel):
    status: str
    confidence: float


class NestedInner(BaseModel):
    label: str
    count: int | None = None


class NestedOuter(BaseModel):
    name: str
    inner: NestedInner
    tags: list[NestedInner]
    extra: str | None = None


def _sent_json_schema(client: MagicMock) -> dict[str, Any]:
    return _create_kwargs(client)["response_format"]["json_schema"]


def test_simple_model_root_sets_additional_properties_false() -> None:
    provider, client = _provider_with_mock(content='{"status":"healthy","confidence":0.9}')

    provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, ProbeResult)

    schema = _sent_json_schema(client)["schema"]
    assert schema["additionalProperties"] is False


def test_simple_model_requires_all_root_properties() -> None:
    provider, client = _provider_with_mock(content='{"status":"healthy","confidence":0.9}')

    provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, ProbeResult)

    schema = _sent_json_schema(client)["schema"]
    assert set(schema["required"]) == set(schema["properties"])


def test_nested_models_in_defs_are_closed() -> None:
    payload = {
        "name": "checkout",
        "inner": {"label": "pool", "count": 1},
        "tags": [{"label": "timeout", "count": None}],
        "extra": None,
    }
    provider, client = _provider_with_mock(content=json.dumps(payload))

    provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, NestedOuter)

    defs = _sent_json_schema(client)["schema"]["$defs"]
    inner = defs["NestedInner"]
    assert inner["additionalProperties"] is False
    assert set(inner["required"]) == set(inner["properties"])


def test_array_item_objects_are_closed() -> None:
    raw = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            }
        },
        "required": ["items"],
    }

    closed = groq_strict_schema(raw)

    item_schema = closed["properties"]["items"]["items"]
    assert item_schema["additionalProperties"] is False
    assert item_schema["required"] == ["id"]


def test_defs_object_models_are_closed() -> None:
    closed = groq_strict_schema(NestedOuter.model_json_schema())

    assert closed["additionalProperties"] is False
    for definition in closed["$defs"].values():
        assert definition["type"] == "object"
        assert definition["additionalProperties"] is False
        assert set(definition["required"]) == set(definition["properties"])


def test_optional_fields_stay_required_and_nullable() -> None:
    closed = groq_strict_schema(NestedOuter.model_json_schema())

    assert "extra" in closed["required"]
    extra = closed["properties"]["extra"]
    assert extra["anyOf"] == [{"type": "string"}, {"type": "null"}]

    inner = closed["$defs"]["NestedInner"]
    assert "count" in inner["required"]
    assert inner["properties"]["count"]["anyOf"] == [
        {"type": "integer"},
        {"type": "null"},
    ]


def test_strict_schema_does_not_mutate_input() -> None:
    original = NestedOuter.model_json_schema()
    snapshot = copy.deepcopy(original)

    closed = groq_strict_schema(original)
    closed["additionalProperties"] = True
    closed["required"] = []
    closed["$defs"]["NestedInner"]["additionalProperties"] = True

    assert original == snapshot
    assert "additionalProperties" not in original
    assert original["$defs"]["NestedInner"].get("additionalProperties") is None


def test_response_format_remains_json_schema_structured_output() -> None:
    provider, client = _provider_with_mock(content='{"status":"healthy","confidence":0.9}')

    provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, ProbeResult)

    response_format = _create_kwargs(client)["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["name"] == "ProbeResult"


def test_strict_mode_remains_enabled_for_nested_models() -> None:
    payload = {
        "name": "checkout",
        "inner": {"label": "pool", "count": None},
        "tags": [],
        "extra": None,
    }
    provider, client = _provider_with_mock(content=json.dumps(payload))

    provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, NestedOuter)

    assert _sent_json_schema(client)["strict"] is True


def test_mocked_probe_response_parses_into_response_model() -> None:
    provider, _ = _provider_with_mock(content='{"status":"healthy","confidence":0.9}')

    result = provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, ProbeResult)

    assert isinstance(result, ProbeResult)
    assert result.status == "healthy"
    assert result.confidence == 0.9


def test_anyof_and_defs_are_walked_without_closing_refs() -> None:
    raw = {
        "type": "object",
        "properties": {
            "choice": {
                "anyOf": [
                    {"$ref": "#/$defs/Alpha"},
                    {"type": "null"},
                ]
            }
        },
        "required": ["choice"],
        "$defs": {
            "Alpha": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            }
        },
    }

    closed = groq_strict_schema(raw)

    assert closed["properties"]["choice"]["anyOf"][0] == {"$ref": "#/$defs/Alpha"}
    assert "additionalProperties" not in closed["properties"]["choice"]["anyOf"][0]
    assert closed["$defs"]["Alpha"]["additionalProperties"] is False
    assert closed["additionalProperties"] is False


def _recommended_action_schema(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties")
    assert isinstance(properties, dict)
    action = properties["recommended_action"]
    assert isinstance(action, dict)
    if "$ref" in action:
        ref = str(action["$ref"]).rsplit("/", 1)[-1]
        defs = schema.get("$defs")
        assert isinstance(defs, dict)
        resolved = defs[ref]
        assert isinstance(resolved, dict)
        return resolved
    return action


def test_hypothesis_schema_exposes_only_supported_actions() -> None:
    provider, client = _provider_with_mock()

    provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)

    schema = _sent_json_schema(client)["schema"]
    action = _recommended_action_schema(schema)
    assert set(action["enum"]) == {
        "rollback_deployment",
        "no_supported_action",
    }
    assert action.get("type") == "string"


def test_hypothesis_schema_rejects_arbitrary_action_via_pydantic() -> None:
    with pytest.raises(ValidationError):
        HypothesisResult.model_validate(
            {
                **SAMPLE_PAYLOAD,
                "recommended_action": "increase_database_pool",
            }
        )
