"""Description lint for the published tool catalog.

Every tool and every parameter a client sees must carry a description written
for it: what it means, the allowed values, units or format, and what happens
when it is omitted. There is no generated fallback (2.0.0), so a parameter
without a description, or with one of the templates the fallback used to
produce, fails here.

The catalog is built the same way the committed snapshots are
(``tests/contracts/catalog_snapshot.py``), for the default composition and
with every optional integration enabled.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from contracts.catalog_snapshot import build_catalog  # noqa: E402

# Templates the removed fallback generated, and other known filler.
FILLER_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^Whether to enable [\w ]+\.$",
        r"^Whether to include [\w ]+ in the response\.$",
        r"^Whether the request should support [\w ]+\.$",
        r"^Whether the [\w ]+ request should be treated as [\w ]+\.$",
        r"^Whether to require [\w ]+\.$",
        r"^Human-readable description for the target resource\.$",
        r"^Value for [\w ]+ used by the [\w ]+ operation\.$",
        r"^Numeric value for [\w ]+\.$",
        r"^Identifiers? for the target [\w ]+\.$",
        r"^Email address(es)? for the [\w ]+\.$",
        r"^Local filesystem path for the [\w ]+\.$",
        r"^URL for the [\w ]+\.$",
        r"^Maximum [\w ]+ to include in the result\.$",
        r"^Minimum [\w ]+ value to allow\.$",
        r"^Display name to use for the target [\w ]+ resource\.$",
        r"^IANA timezone name to use for the operation\.$",
        r"^MIME type for the file or content payload\.$",
        r"^RFC3339 \w+ datetime for this operation\.$",
        r"^(TO|CC|BCC) recipient addresses\.$",
        r"^Search or filter expression used to narrow the results\.$",
        r"^Sort order to apply to the returned results\.$",
        r"^Response format to request from the Google API\.$",
        r"^Display title for the resource being created or updated\.$",
        r"^Attachment payloads to include with the request\.$",
        r"^[\w ]+ for the [\w ]+\.$",
    )
]

_STOPWORDS = {
    "a", "an", "and", "as", "by", "for", "from", "in", "is", "of", "on", "or", "the", "this",
    "to", "with", "id", "ids", "identifier", "identifiers", "value", "values", "parameter",
}

# Parameters whose values form a closed set must publish ``enum``.
ENUM_SHAPED = {
    "corpora", "date_time_render_option", "delete_mode", "export_mime_type", "format",
    "if_exists", "insert_data_option", "label_list_visibility", "major_dimension",
    "message_list_visibility", "mime_type", "on_conflict", "order_by", "range_preset", "response_status",
    "role", "send_updates", "sort_order", "sources", "spaces", "suggestions_view_mode",
    "thumbnail_size", "transparency", "type", "value_input_option", "value_render_option",
    "visibility",
}

# Enum-shaped by name, but the value set is open; the description gives examples.
OPEN_VALUE_SETS = {
    # A comma-separated list of sort keys, each optionally followed by " desc".
    "drive_list_files.order_by",
    # Any MIME type is a valid upload or metadata type.
    "drive_upload_file.mime_type",
    # Export targets depend on the source file's type (Drive about.exportFormats).
    "drive_download_file.export_mime_type",
}

# Tools that expose a raw Google batchUpdate and the simpler tools to try first.
BATCH_UPDATE_SIMPLER = {
    "docs_batch_update_document": ("docs_append_document_text", "docs_replace_document_text"),
    "sheets_batch_update_spreadsheet": ("sheets_update_sheet_values", "sheets_append_sheet_values"),
    "slides_batch_update_presentation": ("slides_replace_text_in_presentation",),
    "forms_batch_update_form": ("forms_set_form_publish_settings",),
}

_STALE_ERROR_CONTRACT = re.compile(r"""\{\s*["']error["']""")
_LEFTOVER_SHAPES = ("Notes:\n", "Input must be an object shaped like")
_ABBREVIATIONS = re.compile(r"\b(e\.g|i\.e|etc|vs)\.", re.IGNORECASE)


def sentences(text: str) -> list[str]:
    """Split prose into sentences, ignoring common abbreviations."""
    masked = _ABBREVIATIONS.sub(lambda match: match.group(0).replace(".", "\0"), text.strip())
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9`'\"(])", masked)
    return [part.replace("\0", ".") for part in parts if part.strip()]


def restates_name(name: str, description: str) -> bool:
    name_words = set(name.lower().split("_"))
    words = {word for word in re.findall(r"[a-z]+", description.lower()) if word not in _STOPWORDS}
    return not words or words <= name_words


def parameter_problems(tool: str, name: str, schema: dict[str, Any]) -> list[str]:
    description = (schema.get("description") or "").strip()
    if not description:
        return [f"{tool}.{name}: no description"]
    problems: list[str] = []
    if any(pattern.match(description) for pattern in FILLER_PATTERNS):
        problems.append(f"{tool}.{name}: filler description {description!r}")
    if restates_name(name, description):
        problems.append(f"{tool}.{name}: description restates the name {description!r}")
    if name in ENUM_SHAPED and f"{tool}.{name}" not in OPEN_VALUE_SETS and not _has_enum(schema):
        problems.append(f"{tool}.{name}: enum-shaped parameter without enum")
    if f"{tool}.{name}" in OPEN_VALUE_SETS and "e.g." not in description:
        problems.append(f"{tool}.{name}: open value set without examples (e.g. ...)")
    return problems


def _has_enum(schema: dict[str, Any]) -> bool:
    if "enum" in schema or "const" in schema:
        return True
    if isinstance(schema.get("items"), dict) and _has_enum(schema["items"]):
        return True
    return any(_has_enum(branch) for branch in schema.get("anyOf", []) if isinstance(branch, dict))


def tool_problems(tool: dict[str, Any], *, default_catalog: bool) -> list[str]:
    name = tool["name"]
    description = (tool.get("description") or "").strip()
    if not description:
        return [f"{name}: no description"]
    problems: list[str] = []
    if _STALE_ERROR_CONTRACT.search(description):
        problems.append(f"{name}: describes the pre-1.0 {{'error': ...}} result contract")
    if any(shape in description for shape in _LEFTOVER_SHAPES):
        problems.append(f"{name}: leftover input-shape note")
    if not default_catalog:
        return problems
    count = len(sentences(description))
    if not 2 <= count <= 5:
        problems.append(f"{name}: {count} sentences (write 3 to 4)")
    if len(description) > 700:
        problems.append(f"{name}: {len(description)} characters (keep it under 700)")
    if not re.search(r"\breturns?\b", description, re.IGNORECASE):
        problems.append(f"{name}: does not say what comes back ('Returns ...')")
    simpler = BATCH_UPDATE_SIMPLER.get(name)
    if simpler is not None:
        opening = " ".join(sentences(description)[:2])
        if not any(sibling in opening for sibling in simpler):
            problems.append(f"{name}: must name {simpler} in its first two sentences")
    return problems


@pytest.fixture(scope="module")
def catalogs() -> dict[str, list[dict[str, Any]]]:
    return {
        "default": build_catalog("catalog_default.json")["tools"],
        "all_optional": build_catalog("catalog_all_optional.json")["tools"],
    }


def test_every_parameter_has_a_real_description(catalogs: dict[str, list[dict[str, Any]]]) -> None:
    problems: list[str] = []
    for tool in catalogs["all_optional"]:
        for name, schema in (tool["inputSchema"].get("properties") or {}).items():
            problems.extend(parameter_problems(tool["name"], name, schema))
    assert problems == []


def test_every_tool_description_follows_the_pattern(catalogs: dict[str, list[dict[str, Any]]]) -> None:
    # The full pattern applies to every core-service tool, extended tier included;
    # the optional integrations only get the contract checks.
    optional = ("apps_", "chat_", "gemini_", "keep_", "meet_")
    default_names = {tool["name"] for tool in catalogs["all_optional"] if not tool["name"].startswith(optional)}
    problems: list[str] = []
    for tool in catalogs["all_optional"]:
        problems.extend(tool_problems(tool, default_catalog=tool["name"] in default_names))
    assert problems == []


def test_open_value_set_exemptions_name_real_parameters(catalogs: dict[str, list[dict[str, Any]]]) -> None:
    published = {
        f"{tool['name']}.{name}"
        for tool in catalogs["all_optional"]
        for name in (tool["inputSchema"].get("properties") or {})
    }
    assert OPEN_VALUE_SETS <= published
    assert set(BATCH_UPDATE_SIMPLER) <= {tool["name"] for tool in catalogs["all_optional"]}


@pytest.mark.parametrize(
    ("name", "description", "flagged"),
    [
        ("unread_only", "Whether to enable unread only.", True),
        ("description", "Human-readable description for the target resource.", True),
        ("local_path", "Local filesystem path for the local.", True),
        ("from_email", "Email address for the from.", True),
        ("message_id", "Message ID.", True),
        ("unread_only", "Only return unread messages; read mail is skipped.", False),
        ("query", "Gmail search query, e.g. 'from:alice newer_than:7d'.", False),
    ],
)
def test_lint_rules_catch_the_known_filler(name: str, description: str, flagged: bool) -> None:
    problems = parameter_problems("tool", name, {"type": "string", "description": description})
    assert bool(problems) is flagged


def test_sentence_splitter_ignores_abbreviations() -> None:
    assert len(sentences("Reads mail, e.g. a thread. Returns text.")) == 2
