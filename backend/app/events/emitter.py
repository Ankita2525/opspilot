from collections.abc import Callable
from datetime import datetime, timezone

from backend.app.events.models import InvestigationEvent, InvestigationEventType

EventPublish = Callable[[InvestigationEvent], None]
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InvestigationEventEmitter:
    """Assigns per-incident sequence numbers and publishes structured events."""

    def __init__(
        self,
        incident_id: str,
        *,
        publish: EventPublish | None = None,
        now: Clock | None = None,
    ) -> None:
        self._incident_id = incident_id
        self._publish = publish
        self._now = now or utc_now
        self._sequence = 0

    def emit(
        self,
        event_type: InvestigationEventType,
        *,
        message: str,
        step: str | None = None,
        data: dict | None = None,
    ) -> InvestigationEvent | None:
        if self._publish is None:
            return None
        self._sequence += 1
        event = InvestigationEvent(
            event_type=event_type,
            incident_id=self._incident_id,
            sequence=self._sequence,
            timestamp=self._now(),
            step=step,
            message=message,
            data=dict(data or {}),
        )
        self._publish(event)
        return event
