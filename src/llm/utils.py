"""Shared LLM response utilities."""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def parse_llm_json(response: str) -> dict | list | None:
    """Extract a JSON object or array from an LLM response.

    Handles:
    - Bare JSON
    - JSON wrapped in markdown code fences (```json ... ```)
    - JSON embedded in surrounding prose

    Returns the parsed value (dict or list), or None on failure.
    """
    text = response.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract the first complete JSON object or array from prose
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

    logger.error("Failed to parse LLM response as JSON")
    return None
