"""Tool specification and schema conversion utilities."""
from __future__ import annotations
import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, get_type_hints

@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    handler: Optional[Callable[..., Any]] = None

    def to_gemini_declaration(self) -> Dict[str, Any]:
        """Convert to Google Gemini function declaration format."""
        schema = dict(self.input_schema)
        # Gemini expects 'parameters' with standard JSON schema
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": schema.get("type", "object"),
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            },
        }

    def to_codex_format(self) -> Dict[str, Any]:
        """Convert to Codex app-bridge compatible tool dictionary."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }

def _py_type_to_json_type(py_type: Any) -> str:
    if py_type in (int, float):
        return "number" if py_type is float else "integer"
    if py_type is bool:
        return "boolean"
    if py_type in (list, tuple, set):
        return "array"
    if py_type in (dict,):
        return "object"
    return "string"

def tool_from_function(func: Callable[..., Any], name: Optional[str] = None, description: Optional[str] = None) -> ToolDefinition:
    """Create a ToolDefinition automatically from a typed Python function."""
    tool_name = name or func.__name__
    doc = description or (inspect.getdoc(func) or f"Execute {tool_name}")
    sig = inspect.signature(func)
    type_hints = get_type_hints(func) if hasattr(func, "__annotations__") else {}

    properties: Dict[str, Any] = {}
    required: List[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls", "ctx"):
            continue
        p_type = type_hints.get(param_name, str)
        json_type = _py_type_to_json_type(p_type)
        properties[param_name] = {"type": json_type, "description": f"Parameter {param_name}"}
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    input_schema = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }

    return ToolDefinition(
        name=tool_name,
        description=doc,
        input_schema=input_schema,
        handler=func,
    )
