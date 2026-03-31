"""Utility functions."""

import os


def expand_path(path: str) -> str:
    """Expand ~ and environment variables in a path."""
    return os.path.expanduser(os.path.expandvars(path))


def safe_backticks(text: str) -> str:
    """Return a backtick fence that doesn't conflict with the text content."""
    fence = "```"
    while fence in text:
        fence += "`"
    return fence
