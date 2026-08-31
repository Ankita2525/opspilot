from backend.app.context.models import EvidenceItem, EvidenceType, IncidentContext
from backend.app.observability.tracing import get_tracer
from backend.app.tools.schemas import DeploymentResponse, LogResponse, MetricResponse

CONTEXT_VERSION = 1
METRIC_RELEVANCE = 0.90
DEPLOYMENT_RELEVANCE_RECENT = 0.85
DEPLOYMENT_RELEVANCE_OLDER = 0.80
LOG_RELEVANCE_ERROR = 0.80
LOG_RELEVANCE_WARNING = 0.55
LOG_RELEVANCE_INFO = 0.25
LOG_RELEVANCE_DEFAULT = 0.40
KEYWORD_BOOST = 0.10
FAILURE_KEYWORDS = (
    "timeout",
    "failed",
    "failure",
    "error",
    "exception",
    "unavailable",
    "exhausted",
    "deadline",
    "401",
    "signature",
)


class ContextManager:
    """Normalize, rank, and bound telemetry into compact model context.

    relevance_score is retrieval/context importance, not causal confidence.
    """

    def __init__(self, max_evidence_items: int = 8) -> None:
        if max_evidence_items < 1:
            raise ValueError("max_evidence_items must be at least 1")
        self._max_evidence_items = max_evidence_items

    def build(
        self,
        *,
        incident_id: str,
        affected_service: str,
        metrics: MetricResponse,
        deployments: list[DeploymentResponse],
        logs: list[LogResponse],
    ) -> IncidentContext:
        with get_tracer().start_as_current_span("opspilot.context.build") as span:
            span.set_attribute("opspilot.incident_id", incident_id)
            span.set_attribute("opspilot.service", affected_service)
            metric_item = _metric_evidence(affected_service, metrics)
            deployment_items = _deployment_evidence(deployments)
            log_items = _log_evidence(affected_service, logs)
            evidence = _bound_evidence(
                metric_item,
                [*deployment_items, *log_items],
                self._max_evidence_items,
            )
            context = IncidentContext(
                incident_id=incident_id,
                affected_service=affected_service,
                symptom_summary=_symptom_summary(affected_service, metrics),
                evidence=evidence,
                recent_changes=deployment_items,
                context_version=CONTEXT_VERSION,
            )
            span.set_attribute("opspilot.evidence_count", len(context.evidence))
            return context


def _symptom_summary(affected_service: str, metrics: MetricResponse) -> str:
    return (
        f"{affected_service} is experiencing p95 latency of "
        f"{metrics.p95_latency_ms} ms and an error rate of "
        f"{metrics.error_rate_percent}%."
    )


def _metric_evidence(affected_service: str, metrics: MetricResponse) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"metric-{affected_service}",
        evidence_type=EvidenceType.METRIC,
        source=affected_service,
        summary=(
            f"p95 latency is {metrics.p95_latency_ms} ms and error rate is "
            f"{metrics.error_rate_percent}%."
        ),
        relevance_score=METRIC_RELEVANCE,
        timestamp=metrics.timestamp,
    )


def _deployment_evidence(
    deployments: list[DeploymentResponse],
) -> list[EvidenceItem]:
    ordered = sorted(deployments, key=lambda event: event.timestamp, reverse=True)
    items: list[EvidenceItem] = []
    for index, event in enumerate(ordered):
        relevance = (
            DEPLOYMENT_RELEVANCE_RECENT if index == 0 else DEPLOYMENT_RELEVANCE_OLDER
        )
        items.append(
            EvidenceItem(
                evidence_id=f"deployment-{event.service}-{event.version}",
                evidence_type=EvidenceType.DEPLOYMENT,
                source=event.service,
                summary=(
                    f"Deployment {event.version} occurred at "
                    f"{event.timestamp.strftime('%H:%M')}."
                ),
                relevance_score=relevance,
                timestamp=event.timestamp,
            )
        )
    return items


def _log_evidence(affected_service: str, logs: list[LogResponse]) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for index, event in enumerate(logs):
        items.append(
            EvidenceItem(
                evidence_id=f"log-{affected_service}-{index}",
                evidence_type=EvidenceType.LOG,
                source=event.service,
                summary=f"{event.level}: {event.message}",
                relevance_score=_log_relevance(event.level, event.message),
                timestamp=event.timestamp,
            )
        )
    return items


def _log_relevance(level: str, message: str) -> float:
    normalized = level.upper()
    if normalized in {"ERROR", "CRITICAL"}:
        score = LOG_RELEVANCE_ERROR
    elif normalized in {"WARN", "WARNING"}:
        score = LOG_RELEVANCE_WARNING
    elif normalized == "INFO":
        score = LOG_RELEVANCE_INFO
    else:
        score = LOG_RELEVANCE_DEFAULT
    haystack = message.lower()
    if any(keyword in haystack for keyword in FAILURE_KEYWORDS):
        score = min(1.0, score + KEYWORD_BOOST)
    return score


def _bound_evidence(
    metric_item: EvidenceItem,
    other_items: list[EvidenceItem],
    max_items: int,
) -> list[EvidenceItem]:
    ranked_others = _rank(other_items)
    remaining = max(0, max_items - 1)
    selected = [metric_item, *ranked_others[:remaining]]
    return _rank(selected)


def _rank(items: list[EvidenceItem]) -> list[EvidenceItem]:
    return sorted(items, key=_evidence_sort_key)


def _evidence_sort_key(item: EvidenceItem) -> tuple[float, float, str]:
    timestamp_rank = (
        -item.timestamp.timestamp() if item.timestamp is not None else float("inf")
    )
    return (-item.relevance_score, timestamp_rank, item.evidence_id)
