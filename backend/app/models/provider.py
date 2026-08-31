from typing import Protocol

from pydantic import BaseModel


class ModelProvider(Protocol):
    """Vendor-neutral interface for structured model output."""

    def generate_structured[T: BaseModel](
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T: ...
