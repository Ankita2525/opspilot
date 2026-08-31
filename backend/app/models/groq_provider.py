from __future__ import annotations

import copy
import json
import os
import re
from typing import Any

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
                    "schema": groq_strict_schema(response_model.model_json_schema()),
                },
            },
            include_reasoning=False,
        )
        content = _message_content(response)
        if not content:
            raise RuntimeError("Groq returned empty structured output.")
        return response_model.model_validate(json.loads(content))


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
