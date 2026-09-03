from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import groq
import pytest
from pydantic import BaseModel, ValidationError

from backend.app.agent.hypotheses import HypothesisResult
from backend.app.models.groq_provider import GroqModelProvider
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


class _Probe(BaseModel):
    status: str


def _completion(content: str | None, *, refusal: str | None = None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)


def _status_error(cls, status_code: int, headers: dict | None = None):
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    return cls(
        message=f"status {status_code}",
        response=response,
        body={"error": {"code": f"code_{status_code}", "type": "api_error"}},
    )


def test_bad_request_not_retried() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = _status_error(groq.BadRequestError, 400)
    provider = GroqModelProvider(api_key="k", client=client, max_attempts=3, base_delay_seconds=0.01)
    with pytest.raises(ModelCallError) as info:
        provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)
    assert info.value.category is ProviderErrorCategory.BAD_REQUEST
    assert info.value.retryable is False
    assert client.chat.completions.create.call_count == 1


def test_auth_errors_not_retried() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = _status_error(groq.AuthenticationError, 401)
    provider = GroqModelProvider(api_key="k", client=client, max_attempts=3, base_delay_seconds=0.01)
    with pytest.raises(ModelCallError) as info:
        provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)
    assert info.value.category is ProviderErrorCategory.AUTH
    assert client.chat.completions.create.call_count == 1


def test_rate_limit_retries_then_fails() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _status_error(groq.RateLimitError, 429, {"retry-after": "0.01"}),
        _status_error(groq.RateLimitError, 429, {"retry-after": "0.01"}),
        _status_error(groq.RateLimitError, 429, {"retry-after": "0.01"}),
    ]
    provider = GroqModelProvider(api_key="k", client=client, max_attempts=3, base_delay_seconds=0.01)
    with pytest.raises(ModelCallError) as info:
        provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)
    assert info.value.category is ProviderErrorCategory.RATE_LIMITED
    assert client.chat.completions.create.call_count == 3


def test_5xx_retries_then_succeeds() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _status_error(groq.InternalServerError, 503),
        _completion(json.dumps(SAMPLE)),
    ]
    provider = GroqModelProvider(api_key="k", client=client, max_attempts=3, base_delay_seconds=0.01)
    result = provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)
    assert result.recommended_action.value == "no_supported_action"
    assert client.chat.completions.create.call_count == 2


def test_timeout_is_retryable() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        groq.APITimeoutError(request=MagicMock()),
        _completion(json.dumps(SAMPLE)),
    ]
    provider = GroqModelProvider(api_key="k", client=client, max_attempts=3, base_delay_seconds=0.01)
    provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)
    assert client.chat.completions.create.call_count == 2


def test_transport_error_is_retryable() -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        groq.APIConnectionError(request=MagicMock(), message="boom"),
        _completion(json.dumps(SAMPLE)),
    ]
    provider = GroqModelProvider(api_key="k", client=client, max_attempts=3, base_delay_seconds=0.01)
    provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)
    assert client.chat.completions.create.call_count == 2


def test_malformed_json_consumes_quota_no_retry() -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = _completion("{not-json")
    provider = GroqModelProvider(api_key="k", client=client, max_attempts=3)
    with pytest.raises(ModelCallError) as info:
        provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)
    assert info.value.category is ProviderErrorCategory.JSON_PARSE
    assert info.value.consume_quota is True
    assert client.chat.completions.create.call_count == 1


def test_validation_failure_consumes_quota_no_retry() -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = _completion('{"status":1}')
    provider = GroqModelProvider(api_key="k", client=client, max_attempts=3)
    with pytest.raises(ModelCallError) as info:
        provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, _Probe)
    assert info.value.category is ProviderErrorCategory.VALIDATION
    assert "status" in info.value.meta.validation_fields
    assert client.chat.completions.create.call_count == 1


def test_refusal_consumes_quota() -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = _completion(None, refusal="blocked")
    provider = GroqModelProvider(api_key="k", client=client)
    with pytest.raises(ModelCallError) as info:
        provider.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)
    assert info.value.category is ProviderErrorCategory.REFUSAL
    assert info.value.consume_quota is True


def test_quota_reserved_once_across_retries() -> None:
    guard = QuotaGuard(
        store=InMemoryQuotaCounterStore(),
        config=QuotaConfig(max_model_calls_per_incident=5),
    )
    inner = MagicMock()
    call_count = {"n": 0}

    def _flaky(*_a, **_k):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise ModelCallError(
                __import__(
                    "backend.app.models.provider_errors", fromlist=["ProviderFailureMeta"]
                ).ProviderFailureMeta(
                    category=ProviderErrorCategory.PROVIDER_5XX,
                    exception_class="InternalServerError",
                    http_status=503,
                    retry_attempt=call_count["n"],
                )
            )
        return HypothesisResult.model_validate(SAMPLE)

    # Retries happen inside GroqModelProvider; Budget sees one logical call.
    groq_client = MagicMock()
    groq_client.chat.completions.create.side_effect = [
        _status_error(groq.InternalServerError, 503),
        _status_error(groq.InternalServerError, 503),
        _completion(json.dumps(SAMPLE)),
    ]
    groq_provider = GroqModelProvider(
        api_key="k", client=groq_client, max_attempts=3, base_delay_seconds=0.01
    )
    budgeted = BudgetGuardedModelProvider(
        groq_provider, guard, session_id="s1", incident_id="i1"
    )
    budgeted.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)
    assert guard._incident_model_calls["i1"] == 1
    assert guard._store.get_counter("global_model_calls", __import__("datetime").datetime.now(__import__("datetime").UTC).date()) == 1


def test_quota_refunds_exhausted_5xx() -> None:
    guard = QuotaGuard(
        store=InMemoryQuotaCounterStore(),
        config=QuotaConfig(max_model_calls_per_incident=5),
    )
    groq_client = MagicMock()
    groq_client.chat.completions.create.side_effect = [
        _status_error(groq.InternalServerError, 503),
        _status_error(groq.InternalServerError, 503),
        _status_error(groq.InternalServerError, 503),
    ]
    groq_provider = GroqModelProvider(
        api_key="k", client=groq_client, max_attempts=3, base_delay_seconds=0.01
    )
    budgeted = BudgetGuardedModelProvider(
        groq_provider, guard, session_id="s1", incident_id="i1"
    )
    with pytest.raises(ModelCallError):
        budgeted.generate_structured(SYSTEM_PROMPT, USER_PROMPT, HypothesisResult)
    assert guard._incident_model_calls.get("i1", 0) == 0
    # Daily counter remains consumed for abuse resistance on transport? Spec says REFUND for 5xx.
    # Refund is per-incident only; global stays. Documented in guard.


def test_quota_consumes_on_validation_failure() -> None:
    guard = QuotaGuard(
        store=InMemoryQuotaCounterStore(),
        config=QuotaConfig(max_model_calls_per_incident=5),
    )
    groq_client = MagicMock()
    groq_client.chat.completions.create.return_value = _completion('{"status":1}')
    groq_provider = GroqModelProvider(api_key="k", client=groq_client)
    budgeted = BudgetGuardedModelProvider(
        groq_provider, guard, session_id="s1", incident_id="i1"
    )
    with pytest.raises(ModelCallError):
        budgeted.generate_structured(SYSTEM_PROMPT, USER_PROMPT, _Probe)
    assert guard._incident_model_calls.get("i1", 0) == 1


def test_failure_logs_never_include_prompts_or_keys(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    client = MagicMock()
    client.chat.completions.create.side_effect = _status_error(groq.BadRequestError, 400)
    provider = GroqModelProvider(api_key="super-secret-key", client=client)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ModelCallError):
            provider.generate_structured(
                "SECRET_SYSTEM gsk_live_should_not_appear",
                "SECRET_USER prompt body",
                HypothesisResult,
            )
    blob = " ".join(record.message for record in caplog.records)
    assert "SECRET_SYSTEM" not in blob
    assert "SECRET_USER" not in blob
    assert "super-secret-key" not in blob
    assert "gsk_live" not in blob
