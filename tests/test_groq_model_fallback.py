from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import groq
import pytest
from pydantic import BaseModel

from backend.app.agent.hypotheses import HypothesisResult
from backend.app.config import OpsPilotSettings
from backend.app.models.generation_meta import generation_meta_from_provider
from backend.app.models.groq_provider import (
    DEFAULT_GROQ_FALLBACK_MODEL,
    DEFAULT_GROQ_MODEL,
    GroqModelProvider,
)
from backend.app.models.provider_errors import (
    ModelCallError,
    ProviderErrorCategory,
)
from backend.app.quotas.budget_provider import BudgetGuardedModelProvider
from backend.app.quotas.guard import InMemoryQuotaCounterStore, QuotaConfig, QuotaGuard

SYSTEM_PROMPT = "Investigate using only supplied evidence."
USER_PROMPT = "synthetic probe evidence only."

SAMPLE = {
    "hypotheses": [
        {
            "cause": "synthetic",
            "confidence": 0.5,
            "evidence": [{"source_type": "log", "summary": "synthetic"}],
        }
    ],
    "recommended_action": "no_supported_action",
    "recommendation_summary": "No automated remediation.",
    "reasoning_summary": "Synthetic evidence only.",
}


def _completion(content: str | None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, refusal=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=None,
    )


def _status_error(
    cls,
    status_code: int,
    *,
    code: str = "code_400",
    err_type: str = "invalid_request_error",
):
    response = MagicMock()
    response.status_code = status_code
    response.headers = {}
    return cls(
        message=f"status {status_code}",
        response=response,
        body={"error": {"code": code, "type": err_type}},
    )


def _json_validate_failed():
    return _status_error(
        groq.BadRequestError,
        400,
        code="json_validate_failed",
        err_type="invalid_request_error",
    )


def _provider(client, **kwargs) -> GroqModelProvider:
    return GroqModelProvider(
        api_key="k",
        client=client,
        model=kwargs.get("model", DEFAULT_GROQ_MODEL),
        fallback_model=kwargs.get("fallback_model", DEFAULT_GROQ_FALLBACK_MODEL),
        max_attempts=kwargs.get("max_attempts", 3),
        base_delay_seconds=0.01,
    )


def test_primary_20b_success_skips_fallback() -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = _completion(json.dumps(SAMPLE))
    provider = _provider(client)
    result = provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)
    assert isinstance(result, HypothesisResult)
    assert client.chat.completions.create.call_count == 1
    assert client.chat.completions.create.call_args.kwargs["model"] == DEFAULT_GROQ_MODEL
    meta = provider.last_generation_meta
    assert meta is not None
    assert meta.fallback_used is False
    assert meta.final_model == DEFAULT_GROQ_MODEL
    assert meta.primary_model == DEFAULT_GROQ_MODEL


def test_exact_json_validate_failed_triggers_single_120b_fallback() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _json_validate_failed(),
        _completion(json.dumps(SAMPLE)),
    ]
    provider = _provider(client)
    result = provider.generate_structured(
        SYSTEM_PROMPT,
        USER_PROMPT,
        HypothesisResult,
        stage="generate_hypothesis",
        incident_id="inc_test",
    )
    assert isinstance(result, HypothesisResult)
    assert client.chat.completions.create.call_count == 2
    models = [c.kwargs["model"] for c in client.chat.completions.create.call_args_list]
    assert models == [DEFAULT_GROQ_MODEL, DEFAULT_GROQ_FALLBACK_MODEL]
    # Identical request semantics except model
    first = client.chat.completions.create.call_args_list[0].kwargs
    second = client.chat.completions.create.call_args_list[1].kwargs
    assert first["messages"] == second["messages"]
    assert first["response_format"] == second["response_format"]
    assert first["include_reasoning"] is False
    assert second["include_reasoning"] is False
    assert first["response_format"]["type"] == "json_schema"
    assert first["response_format"]["json_schema"]["strict"] is True
    meta = provider.last_generation_meta
    assert meta is not None
    assert meta.fallback_used is True
    assert meta.fallback_model == DEFAULT_GROQ_FALLBACK_MODEL
    assert meta.fallback_reason == "json_validate_failed"
    assert meta.final_model == DEFAULT_GROQ_FALLBACK_MODEL


@pytest.mark.parametrize(
    "mutate",
    [
        "model_not_20b",
        "strict_false",
        "type_not_json_schema",
        "status_not_400",
        "code_not_json_validate_failed",
    ],
)
def test_fallback_trigger_precision(mutate: str) -> None:
    client = MagicMock()
    provider_kwargs: dict = {}
    side_effect: list = []

    if mutate == "model_not_20b":
        provider_kwargs["model"] = DEFAULT_GROQ_FALLBACK_MODEL
        side_effect = [_json_validate_failed()]
    elif mutate == "status_not_400":
        side_effect = [
            _status_error(groq.InternalServerError, 503, code="json_validate_failed"),
            _status_error(groq.InternalServerError, 503, code="json_validate_failed"),
            _status_error(groq.InternalServerError, 503, code="json_validate_failed"),
        ]
    elif mutate == "code_not_json_validate_failed":
        side_effect = [_status_error(groq.BadRequestError, 400, code="other_bad_request")]
    elif mutate == "strict_false":
        # Force trigger check by monkeypatching response_format after construction
        side_effect = [_json_validate_failed()]
    elif mutate == "type_not_json_schema":
        side_effect = [_json_validate_failed()]

    client.chat.completions.create.side_effect = side_effect
    provider = _provider(client, **provider_kwargs)

    if mutate in {"strict_false", "type_not_json_schema"}:
        original = provider._should_strict_model_fallback

        def _guard(exc, response_format):
            rf = dict(response_format)
            if mutate == "strict_false":
                rf = {
                    "type": "json_schema",
                    "json_schema": {**rf["json_schema"], "strict": False},
                }
            else:
                rf = {"type": "json_object"}
            return original(exc, rf)

        provider._should_strict_model_fallback = _guard  # type: ignore[method-assign]

    with pytest.raises(ModelCallError):
        provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)

    models = {c.kwargs["model"] for c in client.chat.completions.create.call_args_list}
    if mutate == "model_not_20b":
        assert models == {DEFAULT_GROQ_FALLBACK_MODEL}
        assert client.chat.completions.create.call_count == 1
    elif mutate == "status_not_400":
        # Transient 5xx uses existing retry policy on the primary model only.
        assert models == {DEFAULT_GROQ_MODEL}
        assert client.chat.completions.create.call_count == 3
    else:
        assert models == {DEFAULT_GROQ_MODEL}
        assert client.chat.completions.create.call_count == 1


def test_ordinary_400_does_not_fallback() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _status_error(groq.BadRequestError, 400, code="invalid_schema")
    ]
    provider = _provider(client)
    with pytest.raises(ModelCallError) as info:
        provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)
    assert info.value.category is ProviderErrorCategory.BAD_REQUEST
    assert client.chat.completions.create.call_count == 1
    assert provider.last_generation_meta is not None
    assert provider.last_generation_meta.fallback_used is False


def test_fallback_success_uses_one_logical_quota_reservation() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _json_validate_failed(),
        _completion(json.dumps(SAMPLE)),
    ]
    inner = _provider(client)
    store = InMemoryQuotaCounterStore()
    guard = QuotaGuard(
        store=store,
        config=QuotaConfig(
            max_live_incidents_per_session=3,
            max_model_calls_per_incident=5,
            max_model_calls_per_session_per_day=20,
            global_daily_model_call_cap=500,
        ),
    )
    budgeted = BudgetGuardedModelProvider(
        inner, guard, session_id="s1", incident_id="i1", enforce_budget=True
    )
    result = budgeted.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)
    assert isinstance(result, HypothesisResult)
    assert client.chat.completions.create.call_count == 2
    assert guard._incident_model_calls.get("i1") == 1
    from datetime import UTC, datetime

    assert store.get_counter("global_model_calls", datetime.now(UTC).date()) == 1


def test_fallback_failure_no_third_model_and_refunds_incident_quota() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _json_validate_failed(),
        _status_error(groq.BadRequestError, 400, code="json_validate_failed"),
    ]
    inner = _provider(client)
    store = InMemoryQuotaCounterStore()
    guard = QuotaGuard(
        store=store,
        config=QuotaConfig(
            max_live_incidents_per_session=3,
            max_model_calls_per_incident=5,
            max_model_calls_per_session_per_day=20,
            global_daily_model_call_cap=500,
        ),
    )
    budgeted = BudgetGuardedModelProvider(
        inner, guard, session_id="s1", incident_id="i1", enforce_budget=True
    )
    with pytest.raises(ModelCallError):
        budgeted.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)
    assert client.chat.completions.create.call_count == 2
    models = [c.kwargs["model"] for c in client.chat.completions.create.call_args_list]
    assert models == [DEFAULT_GROQ_MODEL, DEFAULT_GROQ_FALLBACK_MODEL]
    # Logical incident reservation refunded; daily counter remains consumed once
    assert guard._incident_model_calls.get("i1", 0) == 0
    from datetime import UTC, datetime

    assert store.get_counter("global_model_calls", datetime.now(UTC).date()) == 1


def test_fallback_events_are_safe(caplog: pytest.LogCaptureFixture) -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _json_validate_failed(),
        _completion(json.dumps(SAMPLE)),
    ]
    provider = _provider(client)
    with caplog.at_level(logging.INFO):
        provider.generate_structured(
            SYSTEM_PROMPT,
            USER_PROMPT,
            HypothesisResult,
            stage="generate_hypothesis",
            incident_id="inc_safe",
        )
    blob = " ".join(r.message for r in caplog.records)
    assert "primary_model_failed" in blob
    assert "fallback_model_started" in blob
    assert "fallback_model_succeeded" in blob
    assert SYSTEM_PROMPT not in blob
    assert USER_PROMPT not in blob
    assert "gsk_" not in blob
    assert "failed_generation" not in blob
    assert "reasoning" not in blob.lower() or "include_reasoning" in blob


def test_fallback_model_loaded_from_config() -> None:
    settings = OpsPilotSettings.from_env(
        {
            "OPSPILOT_MODEL_PROVIDER": "groq",
            "GROQ_API_KEY": "test-key",
            "GROQ_MODEL": DEFAULT_GROQ_MODEL,
            "OPSPILOT_MODEL_FALLBACK": "openai/gpt-oss-120b",
        }
    )
    provider = settings.create_provider()
    assert isinstance(provider, GroqModelProvider)
    assert provider.fallback_model == "openai/gpt-oss-120b"

    settings2 = OpsPilotSettings.from_env(
        {
            "OPSPILOT_MODEL_PROVIDER": "groq",
            "GROQ_API_KEY": "test-key",
            "GROQ_MODEL": DEFAULT_GROQ_MODEL,
            "OPSPILOT_MODEL_FALLBACK": "custom-fallback-model",
        }
    )
    provider2 = settings2.create_provider()
    assert isinstance(provider2, GroqModelProvider)
    assert provider2.fallback_model == "custom-fallback-model"


def test_provider_call_bound_one_primary_one_fallback() -> None:
    """Logical invocations: 1 primary + 1 fallback. HTTP: 1+1 for this signature."""
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _json_validate_failed(),
        _completion(json.dumps(SAMPLE)),
    ]
    provider = _provider(client, max_attempts=3)
    provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)
    assert client.chat.completions.create.call_count == 2


def test_generation_meta_unwraps_budget_guard() -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = _completion(json.dumps(SAMPLE))
    inner = _provider(client)
    store = InMemoryQuotaCounterStore()
    guard = QuotaGuard(
        store=store,
        config=QuotaConfig(
            max_live_incidents_per_session=3,
            max_model_calls_per_incident=5,
            max_model_calls_per_session_per_day=20,
            global_daily_model_call_cap=500,
        ),
    )
    budgeted = BudgetGuardedModelProvider(
        inner, guard, session_id="s1", incident_id="i1", enforce_budget=True
    )
    budgeted.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)
    meta = generation_meta_from_provider(budgeted)
    assert meta is not None
    assert meta.final_model == DEFAULT_GROQ_MODEL
    assert meta.fallback_used is False
