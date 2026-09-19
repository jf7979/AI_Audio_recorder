"""Scans transcript text for configured trigger phrases (e.g. "flag this")
and captures whatever follows as the flagged snippet.

v1 keeps this to a single segment's text (typically a few seconds to under a
minute of speech) - a trigger said right at the end of a segment won't pull
in the next segment's text. That's an acceptable gap for a personal to-do
capture tool and can be revisited if it turns out to matter in practice.
"""
from __future__ import annotations

import re


def find_flags(text: str, triggers: list[str]) -> list[tuple[str, str]]:
    """Returns (matched_trigger, snippet) pairs, one per trigger phrase found."""
    lowered = text.lower()
    results = []
    for trigger in triggers:
        match = re.search(re.escape(trigger.lower()), lowered)
        if not match:
            continue
        snippet = text[match.end():].strip(" ,.:;-")
        if snippet:
            results.append((trigger, snippet))
    return results
