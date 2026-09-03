from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

from backend.app.models.provider import ModelProvider
from backend.app.models.provider_errors import ModelCallError
from backend.app.quotas.guard import QuotaExceeded, QuotaGuard

if TYPE_CHECKING:
    pass


class AICapacityUnavailable(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class BudgetGuardedModelProvider:
    """Wraps a ModelProvider with quota enforcement before inference.

    Reserves once per logical model stage. Provider-layer HTTP retries must not
    reserve additional public-demo quota.
    """

    def __init__(
        self,
        inner: ModelProvider,
        quota_guard: QuotaGuard,
        *,
        session_id: str | None = None,
        incident_id: str | None = None,
        enforce_budget: bool = True,
    ) -> None:
        self._inner = inner
        self._quota_guard = quota_guard
        self._session_id = session_id
        self._incident_id = incident_id
        self._enforce_budget = enforce_budget

    def with_context(self, *, session_id: str, incident_id: str) -> BudgetGuardedModelProvider:
        return BudgetGuardedModelProvider(
            self._inner,
            self._quota_guard,
            session_id=session_id,
            incident_id=incident_id,
            enforce_budget=self._enforce_budget,
        )

    def generate_structured[T: BaseModel](
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        **kwargs,
    ) -> T:
        if self._enforce_budget:
            if self._quota_guard.is_global_budget_exhausted():
                raise AICapacityUnavailable("ai_provider_capacity")
            if self._session_id is None or self._incident_id is None:
                raise AICapacityUnavailable("ai_capacity_unavailable")
            try:
                self._quota_guard.reserve_model_call(
                    session_id=self._session_id,
                    incident_id=self._incident_id,
                )
            except QuotaExceeded as exc:
                raise AICapacityUnavailable(exc.reason) from exc
            try:
                return self._inner.generate_structured(
                    system_prompt,
                    user_prompt,
                    response_model,
                    **kwargs,
                )
            except ModelCallError as exc:
                if exc.refund_quota and self._incident_id is not None:
                    self._quota_guard.reconcile_failed_incident_call(self._incident_id)
                raise
            except Exception:
                # Unknown failures: refund per-incident reservation; keep daily counters.
                if self._incident_id is not None:
                    self._quota_guard.reconcile_failed_incident_call(self._incident_id)
                raise
        return self._inner.generate_structured(
            system_prompt,
            user_prompt,
            response_model,
            **kwargs,
        )
