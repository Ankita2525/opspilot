from backend.app.events.models import InvestigationEvent
from backend.app.security.untrusted_text import sanitize_public_text, sanitize_public_value


def encode_sse(event: InvestigationEvent) -> str:
    """Encode one investigation event as a standard SSE frame."""
    sanitized = event.model_copy(
        update={
            "message": sanitize_public_text(event.message),
            "data": sanitize_public_value(event.data),
        }
    )
    return (
        f"event: {sanitized.event_type.value}\n"
        f"data: {sanitized.model_dump_json()}\n"
        "\n"
    )
