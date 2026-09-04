from __future__ import annotations

import copy
import json
import logging
import os
import random
import re
import time
from typing import Any

import groq
from groq import Groq
from pydantic import BaseModel, ValidationError

from backend.app.models.generation_meta import StructuredGenerationMeta
from backend.app.models.provider_errors import (
    ModelCallError,
    ProviderErrorCategory,
    ProviderFailureMeta,
)

DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
DEFAULT_GROQ_FALLBACK_MODEL = "openai/gpt-oss-120b"
STRICT_FALLBACK_SOURCE_MODEL = "openai/gpt-oss-20b"
STRICT_FALLBACK_PROVIDER_CODE = "json_validate_failed"
MAX_HTTP_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 0.5
DEFAULT_MAX_DELAY_SECONDS = 8.0

logger = logging.getLogger(__name__)


class GroqModelProvider:
    """Groq-backed ModelProvider using strict JSON Schema structured outputs.

    On the exact proven Groq defect (20b + strict json_schema + HTTP 400 +
    json_validate_failed), performs one model-diverse fallback to the configured
    fallback model (default 120b). No same-model retry for that signature.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_GROQ_MODEL,
        *,
        fallback_model: str | None = None,
        client: Groq | None = None,
        max_attempts: int = MAX_HTTP_ATTEMPTS,
        base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS,
        max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS,
    ) -> None:
        self.model = model
        self.fallback_model = (fallback_model or "").strip() or None
        self._max_attempts = max(1, max_attempts)
        self._base_delay_seconds = base_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self.last_generation_meta: StructuredGenerationMeta | None = None
        if client is not None:
            self._client = client
            return

        resolved_key = api_key if api_key is not None else os.environ.get("GROQ_API_KEY")
        if not resolved_key:
            raise ValueError(
                "Missing Groq API key. Pass api_key or set the GROQ_API_KEY environment variable."
            )
        self._client = Groq(api_key=resolved_key)

    def generate_structured[T: BaseModel](
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        *,
        stage: str | None = None,
        incident_id: str | None = None,
    ) -> T:
        response_format = _strict_response_format(response_model)
        try:
            result = self._generate_with_retries(
                system_prompt,
                user_prompt,
                response_model,
                model=self.model,
                response_format=response_format,
                stage=stage,
                incident_id=incident_id,
            )
        except ModelCallError as primary_exc:
            if not self._should_strict_model_fallback(primary_exc, response_format):
                self.last_generation_meta = StructuredGenerationMeta(
                    provider="groq",
                    primary_model=self.model,
                    final_model=self.model,
                    fallback_used=False,
                )
                logger.warning(
                    "model_call_failed %s",
                    json.dumps(primary_exc.meta.safe_log_dict(), sort_keys=True),
                )
                raise
            assert self.fallback_model is not None
            self._log_fallback_event(
                "primary_model_failed",
                stage=stage,
                incident_id=incident_id,
                primary_model=self.model,
                fallback_model=self.fallback_model,
                http_status=primary_exc.meta.http_status,
                provider_code=primary_exc.meta.provider_code,
                logical_invocation=1,
            )
            self._log_fallback_event(
                "fallback_model_started",
                stage=stage,
                incident_id=incident_id,
                primary_model=self.model,
                fallback_model=self.fallback_model,
                http_status=primary_exc.meta.http_status,
                provider_code=primary_exc.meta.provider_code,
                logical_invocation=2,
            )
            try:
                # Prefer a single HTTP attempt for the model-diverse fallback.
                result = self._generate_once(
                    system_prompt,
                    user_prompt,
                    response_model,
                    model=self.fallback_model,
                    response_format=response_format,
                    attempt=1,
                    stage=stage,
                    incident_id=incident_id,
                )
            except ModelCallError as fallback_exc:
                self._log_fallback_event(
                    "fallback_model_failed",
                    stage=stage,
                    incident_id=incident_id,
                    primary_model=self.model,
                    fallback_model=self.fallback_model,
                    http_status=fallback_exc.meta.http_status,
                    provider_code=fallback_exc.meta.provider_code,
                    logical_invocation=2,
                )
                self.last_generation_meta = StructuredGenerationMeta(
                    provider="groq",
                    primary_model=self.model,
                    final_model=self.fallback_model,
                    fallback_used=True,
                    fallback_model=self.fallback_model,
                    fallback_reason=STRICT_FALLBACK_PROVIDER_CODE,
                )
                logger.warning(
                    "model_call_failed %s",
                    json.dumps(fallback_exc.meta.safe_log_dict(), sort_keys=True),
                )
                raise
            self._log_fallback_event(
                "fallback_model_succeeded",
                stage=stage,
                incident_id=incident_id,
                primary_model=self.model,
                fallback_model=self.fallback_model,
                http_status=None,
                provider_code=STRICT_FALLBACK_PROVIDER_CODE,
                logical_invocation=2,
            )
            self.last_generation_meta = StructuredGenerationMeta(
                provider="groq",
                primary_model=self.model,
                final_model=self.fallback_model,
                fallback_used=True,
                fallback_model=self.fallback_model,
                fallback_reason=STRICT_FALLBACK_PROVIDER_CODE,
            )
            return result

        self.last_generation_meta = StructuredGenerationMeta(
            provider="groq",
            primary_model=self.model,
            final_model=self.model,
            fallback_used=False,
        )
        return result

    def _generate_with_retries[T: BaseModel](
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        *,
        model: str,
        response_format: dict[str, Any],
        stage: str | None,
        incident_id: str | None,
    ) -> T:
        last_error: ModelCallError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._generate_once(
                    system_prompt,
                    user_prompt,
                    response_model,
                    model=model,
                    response_format=response_format,
                    attempt=attempt,
                    stage=stage,
                    incident_id=incident_id,
                )
            except ModelCallError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self._max_attempts:
                    raise
                delay = self._backoff_seconds(attempt, exc.meta.retry_after_seconds)
                logger.info(
                    "model_call_retry %s",
                    json.dumps(
                        {
                            **exc.meta.safe_log_dict(),
                            "next_delay_seconds": round(delay, 3),
                        },
                        sort_keys=True,
                    ),
                )
                time.sleep(delay)
        assert last_error is not None
        raise last_error

    def _should_strict_model_fallback(
        self,
        exc: ModelCallError,
        response_format: dict[str, Any],
    ) -> bool:
        if self.fallback_model is None:
            return False
        if self.fallback_model == self.model:
            return False
        if self.model != STRICT_FALLBACK_SOURCE_MODEL:
            return False
        if response_format.get("type") != "json_schema":
            return False
        json_schema = response_format.get("json_schema")
        if not isinstance(json_schema, dict) or json_schema.get("strict") is not True:
            return False
        if exc.meta.http_status != 400:
            return False
        if exc.meta.provider_code != STRICT_FALLBACK_PROVIDER_CODE:
            return False
        return True

    def _log_fallback_event(
        self,
        event: str,
        *,
        stage: str | None,
        incident_id: str | None,
        primary_model: str,
        fallback_model: str,
        http_status: int | None,
        provider_code: str | None,
        logical_invocation: int,
    ) -> None:
        payload: dict[str, Any] = {
            "event": event,
            "stage": stage,
            "incident_id": incident_id,
            "primary_model": primary_model,
            "fallback_model": fallback_model,
            "logical_invocation": logical_invocation,
        }
        if http_status is not None:
            payload["http_status"] = http_status
        if provider_code:
            payload["provider_code"] = provider_code
        logger.info("model_fallback %s", json.dumps(payload, sort_keys=True))

    def _generate_once[T: BaseModel](
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        *,
        model: str,
        response_format: dict[str, Any],
        attempt: int,
        stage: str | None,
        incident_id: str | None,
    ) -> T:
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=response_format,
                include_reasoning=False,
            )
        except Exception as exc:
            raise _classify_transport_error(
                exc,
                attempt=attempt,
                stage=stage,
                incident_id=incident_id,
            ) from exc

        usage = _token_usage(response)
        content = _message_content(response)
        if _is_refusal(response, content):
            raise ModelCallError(
                ProviderFailureMeta(
                    category=ProviderErrorCategory.REFUSAL,
                    exception_class="Refusal",
                    retry_attempt=attempt,
                    stage=stage,
                    incident_id=incident_id,
                    token_usage=usage,
                    output_length=len(content) if content else 0,
                )
            )
        if not content:
            raise ModelCallError(
                ProviderFailureMeta(
                    category=ProviderErrorCategory.EMPTY_RESPONSE,
                    exception_class="RuntimeError",
                    retry_attempt=attempt,
                    stage=stage,
                    incident_id=incident_id,
                    token_usage=usage,
                    output_length=0,
                ),
                message="Groq returned empty structured output.",
            )
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ModelCallError(
                ProviderFailureMeta(
                    category=ProviderErrorCategory.JSON_PARSE,
                    exception_class="JSONDecodeError",
                    retry_attempt=attempt,
                    stage=stage,
                    incident_id=incident_id,
                    token_usage=usage,
                    output_length=len(content),
                    parse_error_position=exc.pos,
                )
            ) from exc
        try:
            return response_model.model_validate(payload)
        except ValidationError as exc:
            raise ModelCallError(
                ProviderFailureMeta(
                    category=ProviderErrorCategory.VALIDATION,
                    exception_class="ValidationError",
                    retry_attempt=attempt,
                    stage=stage,
                    incident_id=incident_id,
                    token_usage=usage,
                    output_length=len(content),
                    validation_fields=_validation_fields(exc),
                )
            ) from exc

    def _backoff_seconds(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None and retry_after > 0:
            return min(self._max_delay_seconds, float(retry_after))
        delay = min(
            self._max_delay_seconds,
            self._base_delay_seconds * (2 ** (attempt - 1)),
        )
        return delay + random.uniform(0, delay * 0.25)


def _strict_response_format(response_model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": _schema_name(response_model),
            "strict": True,
            "schema": groq_strict_schema(response_model.model_json_schema()),
        },
    }


def groq_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a Groq-strict copy of a JSON Schema object.

    Groq structured outputs require every object node to set
    additionalProperties=false and to list every property in required.
    Optional/nullable fields stay required and keep their null union.
    """

    cloned = copy.deepcopy(schema)
    _close_object_nodes(cloned)
    return cloned


def _close_object_nodes(node: object) -> None:
    if isinstance(node, list):
        for item in node:
            _close_object_nodes(item)
        return
    if not isinstance(node, dict):
        return

    for key, value in node.items():
        if key == "additionalProperties" and not isinstance(value, dict):
            continue
        _close_object_nodes(value)

    if not _is_json_schema_object(node):
        return

    node["additionalProperties"] = False
    properties = node.get("properties")
    if not isinstance(properties, dict) or not properties:
        return
    required = node.get("required")
    if not isinstance(required, list):
        required = []
    else:
        required = [item for item in required if item in properties]
    for name in properties:
        if name not in required:
            required.append(name)
    node["required"] = required


def _is_json_schema_object(node: dict[str, Any]) -> bool:
    if "$ref" in node and "properties" not in node and "type" not in node:
        return False
    schema_type = node.get("type")
    if schema_type == "object":
        return True
    if isinstance(schema_type, list) and "object" in schema_type:
        return True
    return isinstance(node.get("properties"), dict)


def _schema_name(response_model: type[BaseModel]) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", response_model.__name__)
    return cleaned[:64] or "ResponseModel"


def _message_content(response: object) -> str | None:
    choices = getattr(response, "choices", None)
    if not choices:
        return None
    message = getattr(choices[0], "message", None)
    if message is None:
        return None
    content = getattr(message, "content", None)
    return content if isinstance(content, str) else None


def _is_refusal(response: object, content: str | None) -> bool:
    choices = getattr(response, "choices", None)
    if not choices:
        return False
    message = getattr(choices[0], "message", None)
    if message is None:
        return False
    refusal = getattr(message, "refusal", None)
    if isinstance(refusal, str) and refusal.strip():
        return True
    finish = getattr(choices[0], "finish_reason", None)
    return finish == "content_filter"


def _token_usage(response: object) -> dict[str, int] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    payload: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if isinstance(value, int):
            payload[key] = value
    return payload or None


def _validation_fields(exc: ValidationError) -> tuple[str, ...]:
    fields: list[str] = []
    for error in exc.errors():
        loc = error.get("loc") or ()
        parts = [str(item) for item in loc if item != "__root__"]
        if parts:
            fields.append(".".join(parts))
    return tuple(dict.fromkeys(fields))


def _classify_transport_error(
    exc: Exception,
    *,
    attempt: int,
    stage: str | None,
    incident_id: str | None,
) -> ModelCallError:
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    provider_code = None
    provider_type = None
    retry_after = _retry_after_seconds(exc)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        if isinstance(err, dict):
            code = err.get("code")
            err_type = err.get("type")
            provider_code = str(code) if code is not None else None
            provider_type = str(err_type) if err_type is not None else None

    if isinstance(exc, groq.RateLimitError) or status == 429:
        category = ProviderErrorCategory.RATE_LIMITED
    elif isinstance(exc, (groq.AuthenticationError, groq.PermissionDeniedError)) or status in {
        401,
        403,
    }:
        category = ProviderErrorCategory.AUTH
    elif isinstance(exc, groq.BadRequestError) or status == 400:
        category = ProviderErrorCategory.BAD_REQUEST
    elif isinstance(exc, groq.APITimeoutError) or isinstance(exc, TimeoutError):
        category = ProviderErrorCategory.TIMEOUT
    elif isinstance(exc, groq.APIConnectionError):
        category = ProviderErrorCategory.NETWORK
    elif isinstance(exc, groq.InternalServerError) or (
        isinstance(status, int) and status >= 500
    ):
        category = ProviderErrorCategory.PROVIDER_5XX
    elif isinstance(exc, groq.APIStatusError) and isinstance(status, int):
        if status == 429:
            category = ProviderErrorCategory.RATE_LIMITED
        elif status in {401, 403}:
            category = ProviderErrorCategory.AUTH
        elif status == 400:
            category = ProviderErrorCategory.BAD_REQUEST
        elif status >= 500:
            category = ProviderErrorCategory.PROVIDER_5XX
        else:
            category = ProviderErrorCategory.UNKNOWN
    else:
        category = ProviderErrorCategory.UNKNOWN

    return ModelCallError(
        ProviderFailureMeta(
            category=category,
            exception_class=type(exc).__name__,
            http_status=status if isinstance(status, int) else None,
            provider_code=provider_code,
            provider_type=provider_type,
            retry_attempt=attempt,
            stage=stage,
            incident_id=incident_id,
            retry_after_seconds=retry_after,
        )
    )


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
