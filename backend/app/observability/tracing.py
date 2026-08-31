from opentelemetry import trace
from opentelemetry.trace import Tracer

INSTRUMENTATION_SCOPE = "opspilot"


def get_tracer() -> Tracer:
    """Return the OpsPilot tracer. No-op unless a TracerProvider is configured."""
    return trace.get_tracer(INSTRUMENTATION_SCOPE)
