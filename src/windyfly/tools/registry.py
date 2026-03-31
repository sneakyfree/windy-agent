"""Tool registry — registration, schema generation, and dispatch.

Tools are callable functions that the agent can invoke via LLM
function calling. Each tool has a name, description, parameter
schema, and implementation function.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry for agent-callable tools.

    Manages tool functions, generates OpenAI-format schemas,
    and dispatches tool calls.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Callable] = {}
        self._schemas: list[dict[str, Any]] = []

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        fn: Callable,
    ) -> None:
        """Register a tool.

        Args:
            name: Tool name (must be unique).
            description: Human-readable description for the LLM.
            parameters: JSON Schema for the tool's parameters.
            fn: The callable implementation.
        """
        self._tools[name] = fn
        self._schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        })
        logger.debug("Registered tool: %s", name)

    def get_schemas(self) -> list[dict[str, Any]]:
        """Get all tool schemas in OpenAI function-calling format."""
        return self._schemas.copy()

    def execute(self, name: str, arguments: dict[str, Any] | str) -> str:
        """Execute a registered tool by name.

        Args:
            name: Tool name to execute.
            arguments: Tool arguments (dict or JSON string).

        Returns:
            Result as a string.

        Raises:
            KeyError: If tool not found.
        """
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as e:
                logger.error("Tool '%s' received malformed JSON: %s", name, e)
                return json.dumps({"error": f"Malformed arguments: {e}"})

        try:
            result = self._tools[name](**arguments)
            if isinstance(result, str):
                return result
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            logger.error("Tool '%s' failed: %s", name, e)
            return json.dumps({"error": str(e)})

    @property
    def tool_count(self) -> int:
        """Number of registered tools."""
        return len(self._tools)
