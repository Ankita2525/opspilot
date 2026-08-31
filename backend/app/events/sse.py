from backend.app.events.models import InvestigationEvent


def encode_sse(event: InvestigationEvent) -> str:
    """Encode one investigation event as a standard SSE frame."""
    return (
        f"event: {event.event_type.value}\n"
        f"data: {event.model_dump_json()}\n"
        "\n"
    )
