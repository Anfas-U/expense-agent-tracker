"""Tool registry and schema generator for hand-crafted agent loop."""

import inspect
import json
from typing import Any, Callable, Dict, List, Optional, get_type_hints
from pydantic import BaseModel


class ToolDefinition(BaseModel):
    """Metadata and schema for an agent tool."""
    name: str
    description: str
    parameters: Dict[str, Any]
    func: Any


# Global in-memory tool registry
_TOOL_REGISTRY: Dict[str, ToolDefinition] = {}


def python_type_to_json_type(py_type: Any) -> str:
    """Map python types to JSON Schema types."""
    if py_type in (str, Optional[str]):
        return "string"
    elif py_type in (int, Optional[int]):
        return "integer"
    elif py_type in (float, Optional[float]):
        return "number"
    elif py_type in (bool, Optional[bool]):
        return "boolean"
    elif py_type in (list, List) or hasattr(py_type, "__origin__") and py_type.__origin__ is list:
        return "array"
    elif py_type in (dict, Dict) or hasattr(py_type, "__origin__") and py_type.__origin__ is dict:
        return "object"
    return "string"


def register_tool(name: Optional[str] = None, description: Optional[str] = None):
    """Decorator to register a Python function as an agent tool with JSON schema."""
    def decorator(func: Callable):
        tool_name = name or func.__name__
        tool_desc = description or (func.__doc__ or "").strip().split("\n")[0]
        
        # Build JSON Schema parameters from type hints and docstring
        sig = inspect.signature(func)
        try:
            type_hints = get_type_hints(func)
        except Exception:
            type_hints = {}

        properties: Dict[str, Any] = {}
        required: List[str] = []

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue

            param_type = type_hints.get(param_name, str)
            json_type = python_type_to_json_type(param_type)
            
            param_meta: Dict[str, Any] = {"type": json_type}
            
            # Check default value
            if param.default is inspect.Parameter.empty:
                required.append(param_name)
            else:
                param_meta["default"] = param.default

            properties[param_name] = param_meta

        parameters_schema = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

        tool_def = ToolDefinition(
            name=tool_name,
            description=tool_desc,
            parameters=parameters_schema,
            func=func,
        )
        _TOOL_REGISTRY[tool_name] = tool_def
        return func

    return decorator


def get_tool(name: str) -> Optional[ToolDefinition]:
    """Retrieve registered tool by name."""
    return _TOOL_REGISTRY.get(name)


def get_all_tools() -> Dict[str, ToolDefinition]:
    """Retrieve all registered tools."""
    return _TOOL_REGISTRY


def get_tool_schemas() -> List[Dict[str, Any]]:
    """Get OpenAPI/JSON schemas for all registered tools formatted for LLM function calling."""
    schemas = []
    for tool_def in _TOOL_REGISTRY.values():
        schemas.append({
            "name": tool_def.name,
            "description": tool_def.description,
            "parameters": tool_def.parameters,
        })
    return schemas


def execute_tool(name: str, arguments: Dict[str, Any]) -> Any:
    """Execute a registered tool by name with provided arguments."""
    tool = get_tool(name)
    if not tool:
        raise ValueError(f"Tool '{name}' is not registered.")
    
    # Execute tool
    return tool.func(**arguments)

