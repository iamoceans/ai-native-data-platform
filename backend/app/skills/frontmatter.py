"""Strict front matter for a SKILL.md package (design 7.2).

Every field is untrusted user content, so the rules are closed:

* the top level allows `name`, `description`, `license`, `compatibility` and
  `metadata`; anything else is rejected with an explanation instead of ignored;
* YAML is parsed with duplicate keys, aliases/anchors, custom tags and deep
  nesting rejected - a bundle cannot smuggle structure through YAML features;
* `metadata.askdata` is a closed schema (`extra="forbid"`); an unsupported
  `schema_version` or an unknown field is an error, not a warning;
* other `metadata` namespaces are kept verbatim as display data and are never
  interpreted.
"""

from __future__ import annotations

import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.constants import ErrorCode

NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 500
MAX_YAML_DEPTH = 12
ASKADATA_SCHEMA_VERSION = 1

# What a skill may require the turn to resolve before it applies (design 7.2).
KNOWN_REQUIRED_INPUTS = frozenset(
    {"metric_key", "baseline_period", "current_period", "dimensions", "filters"}
)
TOP_LEVEL_FIELDS = frozenset({"name", "description", "license", "compatibility", "metadata"})

# Constructs that must never reach a renderer as markup (design 11.6).
UNSAFE_MARKUP_PATTERNS = (
    re.compile(r"<\s*script", re.IGNORECASE),
    re.compile(r"<\s*iframe", re.IGNORECASE),
    re.compile(r"<\s*object", re.IGNORECASE),
    re.compile(r"<\s*embed", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"data\s*:\s*text/html", re.IGNORECASE),
)


class SkillError(Exception):
    """A skill package problem, already mapped to its API error code."""

    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class AskdataExtension(BaseModel):
    """The only namespace the platform interprets (design 7.2)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = ASKADATA_SCHEMA_VERSION
    metric_keys: list[str] = Field(default_factory=list, max_length=32)
    preferred_dimensions: list[str] = Field(default_factory=list, max_length=16)
    required_inputs: list[str] = Field(default_factory=list, max_length=16)
    references: list[str] = Field(default_factory=list, max_length=32)


class SkillFrontmatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class _DuplicateKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys."""


def _construct_mapping(loader: _DuplicateKeyLoader, node, deep: bool = False) -> dict:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise SkillError(
                ErrorCode.SKILL_INVALID, f"duplicate key in front matter: {key!r}"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_DuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _scan_yaml_events(text: str) -> None:
    """Reject YAML features a data-only document never needs (design 7.2)."""
    depth = 0
    try:
        for event in yaml.parse(text):
            if isinstance(event, yaml.AliasEvent):
                raise SkillError(ErrorCode.SKILL_INVALID, "front matter may not use aliases or anchors")
            tag = getattr(event, "tag", None)
            if tag and not str(tag).startswith("tag:yaml.org,2002:"):
                raise SkillError(ErrorCode.SKILL_INVALID, f"front matter may not use the tag {tag!r}")
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                depth += 1
                if depth > MAX_YAML_DEPTH:
                    raise SkillError(ErrorCode.SKILL_INVALID, "front matter is nested too deeply")
            elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                depth -= 1
    except yaml.YAMLError as exc:
        raise SkillError(ErrorCode.SKILL_INVALID, f"front matter is not valid YAML: {exc}") from exc


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return `(yaml_text, body)`; the file must open with a `---` block."""
    if not text.startswith("---"):
        raise SkillError(ErrorCode.SKILL_INVALID, "SKILL.md must start with a --- front matter block")
    lines = text.split("\n")
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        raise SkillError(ErrorCode.SKILL_INVALID, "front matter is not closed with ---")
    yaml_text = "\n".join(lines[1:end_index])
    body = "\n".join(lines[end_index + 1 :]).strip()
    return yaml_text, body


def parse_frontmatter(text: str) -> tuple[SkillFrontmatter, AskdataExtension | None, str]:
    """Parse and validate the front matter and the body."""
    yaml_text, body = split_frontmatter(text)
    _scan_yaml_events(yaml_text)
    try:
        loaded = yaml.load(yaml_text, Loader=_DuplicateKeyLoader)
    except SkillError:
        raise
    except yaml.YAMLError as exc:
        raise SkillError(ErrorCode.SKILL_INVALID, f"front matter is not valid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise SkillError(ErrorCode.SKILL_INVALID, "front matter must be a mapping")

    unknown = sorted(set(loaded) - TOP_LEVEL_FIELDS)
    if unknown:
        raise SkillError(
            ErrorCode.SKILL_INVALID,
            f"unsupported front matter fields: {', '.join(unknown)}",
            details={"allowed": sorted(TOP_LEVEL_FIELDS)},
        )
    try:
        frontmatter = SkillFrontmatter.model_validate(loaded)
    except ValidationError as exc:
        raise SkillError(
            ErrorCode.SKILL_INVALID, f"front matter is invalid: {exc.errors()[0]['msg']}"
        ) from exc

    if not NAME_PATTERN.match(frontmatter.name) or len(frontmatter.name) > MAX_NAME_LENGTH:
        raise SkillError(
            ErrorCode.SKILL_INVALID,
            "name must be 1-64 characters of lowercase letters, digits and single hyphens",
        )
    if not 1 <= len(frontmatter.description) <= MAX_DESCRIPTION_LENGTH:
        raise SkillError(
            ErrorCode.SKILL_INVALID,
            f"description must be 1-{MAX_DESCRIPTION_LENGTH} characters",
        )
    if not body:
        raise SkillError(ErrorCode.SKILL_INVALID, "SKILL.md must have a non-empty body")
    for pattern in UNSAFE_MARKUP_PATTERNS:
        if pattern.search(body):
            raise SkillError(
                ErrorCode.SKILL_UNSUPPORTED,
                "SKILL.md may not contain scriptable markup or unsafe URLs",
            )

    extension: AskdataExtension | None = None
    askdata = (frontmatter.metadata or {}).get("askdata")
    if askdata is not None:
        if not isinstance(askdata, dict):
            raise SkillError(ErrorCode.SKILL_INVALID, "metadata.askdata must be a mapping")
        try:
            extension = AskdataExtension.model_validate(askdata)
        except ValidationError as exc:
            raise SkillError(
                ErrorCode.SKILL_INVALID,
                f"metadata.askdata is invalid: {exc.errors()[0]['msg']}",
            ) from exc
        if extension.schema_version != ASKADATA_SCHEMA_VERSION:
            raise SkillError(
                ErrorCode.SKILL_INVALID,
                f"metadata.askdata.schema_version {extension.schema_version} is not supported",
                details={"supported": ASKADATA_SCHEMA_VERSION},
            )
        unknown_inputs = sorted(set(extension.required_inputs) - KNOWN_REQUIRED_INPUTS)
        if unknown_inputs:
            raise SkillError(
                ErrorCode.SKILL_INVALID,
                f"required_inputs names fields the platform does not resolve: {', '.join(unknown_inputs)}",
                details={"allowed": sorted(KNOWN_REQUIRED_INPUTS)},
            )
    return frontmatter, extension, body
