"""Check that a script only says what the content manifest shows (synthetic sample).

The content manifest (``content_manifest.json``) is the single source of truth for the
values shown on screen. The script (``script.json``) is checked against it:

  {"date": "YYYY-MM-DD",
   "items": [{"id": "item-1", "label": "項目1", "valid_until": "<ISO 8601, optional>",
              "values": {"count": 3, "rate": 12.5}}],
   "allowed_numbers": ["2"]}                      # optional, e.g. step numbers read aloud

  {"lines": [{"id": "L01", "text": "...",
              "claims": [{"id": "c1", "kind": "value", "value": 12.5,
                          "manifest_path": "items.0.values.rate"},
                         {"id": "c2", "kind": "compare", "op": "gt",
                          "left_path": "items.0.values.rate",
                          "right_path": "items.1.values.rate"}]}]}

Rules (each failure has a stable code):
  NP_NUMERIC  a number in the text is neither typed by a claim nor found in the manifest,
              or a "value" claim differs from the manifest / is missing from the text
  NP_TARGET   the text refers to an item label that the manifest does not have
  NP_COUNT    the text states a total count that differs from the number of items
  NP_LOGIC    a "compare" claim does not hold for the manifest values
Nothing here interprets free text beyond these explicit patterns: anything that cannot
be decided is reported as a failure (fail-closed).
"""
from __future__ import annotations

import operator
import re
from typing import Any

NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?")
LABEL_REF = re.compile(r"項目\d+")
TOTAL_COUNT = re.compile(r"全(\d+)件")
# Numbers that only count dates are not facts about the items.
DATE_SUFFIX = re.compile(r"(?:月|日)")
OPS = {"gt": operator.gt, "ge": operator.ge, "lt": operator.lt, "le": operator.le}


def get_path(value: object, path: str) -> object:
    """Resolve ``items.0.values.rate`` style paths. Returns None when missing."""
    current = value
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(str(value).rstrip("%"))
    except (TypeError, ValueError):
        return None


def _leaf_strings(value: object):
    if isinstance(value, dict):
        for child in value.values():
            yield from _leaf_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _leaf_strings(child)
    elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
        yield str(value)


def manifest_numbers(manifest: dict) -> set[str]:
    """Every number that is visible in the manifest, plus the item count."""
    numbers: set[str] = set()
    for leaf in _leaf_strings(manifest.get("items", [])):
        numbers.update(NUMBER.findall(leaf))
    numbers.add(str(len(manifest.get("items", []))))
    numbers.update(str(x) for x in manifest.get("allowed_numbers", []))
    return numbers


def _same_number(raw: str, known: set[str]) -> bool:
    value = _as_float(raw)
    return raw in known or (value is not None and any(_as_float(k) == value for k in known))


def validate_claims(manifest: dict, script: dict) -> list[dict]:
    """Return a list of failures (empty list means the script matches the manifest)."""
    failures: list[dict] = []
    items = manifest.get("items", [])
    known = manifest_numbers(manifest)
    labels = {str(item.get("label")) for item in items if isinstance(item, dict)}
    for index, line in enumerate(script.get("lines", [])):
        lid = line.get("id", f"L{index + 1}")
        text = str(line.get("text", ""))
        typed: set[str] = set()
        for claim in line.get("claims", []):
            kind = claim.get("kind")
            if kind == "value":
                expected = _as_float(claim.get("value"))
                shown = _as_float(get_path(manifest, str(claim.get("manifest_path", ""))))
                in_text = any(_as_float(n) == expected for n in NUMBER.findall(text))
                typed.add(str(claim.get("value")))
                if expected is None or shown is None or shown != expected or not in_text:
                    failures.append({"code": "NP_NUMERIC", "line": lid, "claim_id": claim.get("id"),
                                     "message": "value claim does not match the manifest or the text"})
            elif kind == "compare":
                left = _as_float(get_path(manifest, str(claim.get("left_path", ""))))
                right = _as_float(get_path(manifest, str(claim.get("right_path", ""))))
                op = OPS.get(str(claim.get("op")))
                if left is None or right is None or op is None or not op(left, right):
                    failures.append({"code": "NP_LOGIC", "line": lid, "claim_id": claim.get("id"),
                                     "message": "compare claim does not hold for the manifest values"})
            else:
                failures.append({"code": "NP_LOGIC", "line": lid, "claim_id": claim.get("id"),
                                 "message": "unknown claim kind"})
        for match in NUMBER.finditer(text):
            raw = match.group(0)
            if raw in typed or DATE_SUFFIX.match(text[match.end():match.end() + 1]):
                continue
            if not _same_number(raw, known):
                failures.append({"code": "NP_NUMERIC", "line": lid, "number": raw,
                                 "message": "number is not in the manifest"})
        for ref in LABEL_REF.findall(text):
            if ref not in labels:
                failures.append({"code": "NP_TARGET", "line": lid, "target": ref,
                                 "message": "text refers to an item the manifest does not have"})
        for total in TOTAL_COUNT.findall(text):
            if int(total) != len(items):
                failures.append({"code": "NP_COUNT", "line": lid, "claimed": int(total),
                                 "shown": len(items), "message": "stated total differs from the item count"})
    return failures
