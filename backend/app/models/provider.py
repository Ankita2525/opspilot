from typing import Protocol, runtime_checkable

from pydantic import BaseModel


@runtime_checkable
class ModelProvider(Protocol):
    """Vendor-neutral interface for structured model output."""

    def generate_structured[T: BaseModel](
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T: ...
