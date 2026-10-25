"""Frozen public schemas, validated through Pydantic without coercing MCP inputs.

The JSON schemas are the 0.3.0 wire contract. Keeping them explicit prevents a
framework upgrade from changing optionality, defaults or camelCase names.
"""

import json
from copy import deepcopy
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import RootModel, ValidationInfo, model_validator


def contracts() -> list[dict[str, Any]]:
    return json.loads(
        files("agility_mcp").joinpath("tool_contracts.json").read_text(encoding="utf-8")
    )


def defaults(value: Any, schema: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        value = deepcopy(value)
        for key, definition in schema.get("properties", {}).items():
            if key not in value and "default" in definition:
                value[key] = deepcopy(definition["default"])
            if key in value:
                value[key] = defaults(value[key], definition)
    elif isinstance(value, list):
        value = [defaults(child, schema.get("items", {})) for child in value]
    return value


class ToolArguments(RootModel[dict[str, Any]]):
    @model_validator(mode="after")
    def validate_contract(self, info: ValidationInfo) -> "ToolArguments":
        schema = (info.context or {})["schema"]
        if not Draft202012Validator(schema).is_valid(self.root):
            raise ValueError("Invalid tool arguments.")
        self.root = defaults(self.root, schema)
        # Refinements that cannot be expressed by the original Zod JSON schema.
        if len(self.root.get("attributes", {})) > 100:
            raise ValueError("At most 100 attributes.")
        for relation in self.root.get("relations", []):
            if (
                len(relation["targets"]) > 1
                if relation["action"] == "set"
                else not relation["targets"]
            ):
                raise ValueError(
                    "set accepts zero or one target; add/remove require at least one target."
                )
        json.dumps(self.root, allow_nan=False)
        return self
