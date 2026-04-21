"""Utility functions."""

import json
import os
from typing import Any


def expand_path(path: str) -> str:
    """Expand ~ and environment variables in a path."""
    return os.path.expanduser(os.path.expandvars(path))


def safe_backticks(text: str) -> str:
    """Return a backtick fence that doesn't conflict with the text content."""
    fence = "```"
    while fence in text:
        fence += "`"
    return fence


def unescape_json_strings(obj: Any) -> Any:
    """Recursively try to parse JSON strings inside dicts/lists so they print cleanly."""
    if isinstance(obj, str):
        try:
            return unescape_json_strings(json.loads(obj))
        except (json.JSONDecodeError, TypeError):
            return obj
    if isinstance(obj, dict):
        return {k: unescape_json_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [unescape_json_strings(i) for i in obj]
    return obj


def pretty_raw(data: Any) -> str:
    """Format raw data for logging: readable, no double-escaped JSON."""
    if isinstance(data, str):
        return data
    data = unescape_json_strings(data)
    return json.dumps(data, indent=2, ensure_ascii=False)
