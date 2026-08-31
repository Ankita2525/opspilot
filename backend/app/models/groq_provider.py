from __future__ import annotations

import json
import os
import re

from groq import Groq
from pydantic import BaseModel

DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"


class GroqModelProvider:
    """Groq-backed ModelProvider using strict JSON Schema structured outputs."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_GROQ_MODEL,
        *,
        client: Groq | None = None,
    ) -> None:
        self.model = model
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
    ) -> T:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": _schema_name(response_model),
                    "strict": True,
                    "schema": response_model.model_json_schema(),
                },
            },
            include_reasoning=False,
        )
        content = _message_content(response)
        if not content:
            raise RuntimeError("Groq returned empty structured output.")
        return response_model.model_validate(json.loads(content))


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
