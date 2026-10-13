"""Skill packages: parse, validate and hash a SKILL.md or a bounded ZIP (design 7).

Everything here is pure: it takes bytes plus the configured limits and returns a
`ParsedSkill` or raises `SkillError`. The caller (service/route) adds the
database-backed checks (does the metric exist, is the dimension allowlisted).

Two properties matter more than the parsing itself:

* **Nothing is written anywhere.** The archive is read in memory with hard caps
  applied *while reading*, so a zip bomb or a symlink cannot reach the disk, and
  a rejected package leaves no half-imported state.
* **The hash is deterministic.** It is computed over the sorted normalized paths
  and the BOM-stripped, LF-normalized contents, so the same package re-uploaded
  from Windows or Linux produces the same version.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from app.config import Settings
from app.constants import ErrorCode
from app.skills.frontmatter import SkillError, parse_frontmatter

SKILL_FILENAME = "SKILL.md"
ALLOWED_REFERENCE_SUFFIXES = (".md", ".txt")
MEDIA_TYPES = {".md": "text/markdown", ".txt": "text/plain"}
ARCHIVE_SUFFIXES = (".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar")
WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{n}" for n in range(1, 10)),
    *(f"lpt{n}" for n in range(1, 10)),
}
DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class PackageFile:
    path: str
    media_type: str
    content: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ParsedSkill:
    """A validated package, ready to become an immutable skill version."""

    name: str
    description: str
    body: str
    frontmatter: dict[str, Any]
    files: tuple[PackageFile, ...]
    content_hash: str
    manifest: dict[str, Any]
    metric_keys: tuple[str, ...] = ()
    preferred_dimensions: tuple[str, ...] = ()
    required_inputs: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    display_metadata: dict[str, Any] = field(default_factory=dict)


def _normalize_text(raw: bytes) -> str:
    """UTF-8, no BOM, LF endings - the canonical form used for hashing."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SkillError(ErrorCode.SKILL_INVALID, "package files must be UTF-8 text") from exc
    if "\x00" in text:
        raise SkillError(ErrorCode.SKILL_INVALID, "package files may not contain NUL bytes")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _check_path(raw_name: str, *, settings: Settings) -> str:
    """Normalize an archive member path or reject it (design 7.3)."""
    name = raw_name.replace("\\", "/")
    if not name or name.endswith("/"):
        return ""  # a directory entry: nothing to read
    if "\x00" in name:
        raise SkillError(ErrorCode.SKILL_UNSUPPORTED, "package contains a NUL byte in a path")
    if name.startswith("/") or name.startswith("//") or DRIVE_PATTERN.match(name):
        raise SkillError(ErrorCode.SKILL_UNSUPPORTED, f"package contains an absolute path: {raw_name!r}")
    parts = PurePosixPath(name).parts
    if any(part in ("..", ".") for part in parts):
        raise SkillError(ErrorCode.SKILL_UNSUPPORTED, f"package path escapes the package: {raw_name!r}")
    if len(parts) > settings.skill_path_max_depth:
        raise SkillError(ErrorCode.SKILL_UNSUPPORTED, f"package path is nested too deeply: {raw_name!r}")
    for part in parts:
        if ":" in part:
            raise SkillError(ErrorCode.SKILL_UNSUPPORTED, f"package path uses an alternate data stream: {raw_name!r}")
        if part.rstrip(" .") != part:
            raise SkillError(ErrorCode.SKILL_UNSUPPORTED, f"package path has a trailing dot or space: {raw_name!r}")
        stem = part.split(".", 1)[0].lower()
        if stem in WINDOWS_RESERVED:
            raise SkillError(ErrorCode.SKILL_UNSUPPORTED, f"package path uses a reserved name: {raw_name!r}")
    normalized = str(PurePosixPath(*parts))
    if len(normalized) > settings.skill_path_max_length:
        raise SkillError(ErrorCode.SKILL_UNSUPPORTED, f"package path is too long: {raw_name!r}")
    return normalized


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return (mode & 0xA000) == 0xA000


def _read_bounded(archive: zipfile.ZipFile, info: zipfile.ZipInfo, *, cap: int, settings: Settings) -> bytes:
    """Read one member with the compression ratio and byte caps enforced live."""
    if info.compress_size and info.file_size / max(info.compress_size, 1) > settings.skill_compression_ratio_max:
        raise SkillError(
            ErrorCode.SKILL_TOO_LARGE,
            f"{info.filename!r} exceeds the compression ratio limit",
        )
    if info.file_size > cap:
        raise SkillError(ErrorCode.SKILL_TOO_LARGE, f"{info.filename!r} exceeds its size limit")
    chunks: list[bytes] = []
    total = 0
    with archive.open(info) as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > cap:
                raise SkillError(ErrorCode.SKILL_TOO_LARGE, f"{info.filename!r} exceeds its size limit")
            chunks.append(chunk)
    return b"".join(chunks)


def _media_type(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    return MEDIA_TYPES.get(suffix, "application/octet-stream")


def _is_reference(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return len(parts) >= 2 and parts[0] == "references"


def _strip_single_root(paths: list[str]) -> list[str]:
    """Allow exactly one top-level directory around SKILL.md (design 7.1)."""
    firsts = {PurePosixPath(path).parts[0] for path in paths}
    if SKILL_FILENAME in paths:
        return paths
    if len(firsts) == 1:
        root = next(iter(firsts))
        return [
            str(PurePosixPath(*PurePosixPath(path).parts[1:]))
            for path in paths
            if len(PurePosixPath(path).parts) > 1
        ]
    return paths


def _content_hash(files: tuple[PackageFile, ...]) -> str:
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda entry: entry.path):
        digest.update(item.path.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(len(item.content.encode("utf-8"))).encode("ascii"))
        digest.update(b"\x00")
        digest.update(item.content.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _build_parsed_skill(
    *, skill_md: str, files: tuple[PackageFile, ...], display_metadata: dict[str, Any]
) -> ParsedSkill:
    frontmatter, extension, body = parse_frontmatter(skill_md)
    if extension is not None:
        known_references = {item.path for item in files}
        missing = sorted(set(extension.references) - known_references)
        if missing:
            raise SkillError(
                ErrorCode.SKILL_INVALID,
                f"references listed in front matter are not in the package: {', '.join(missing)}",
            )
    return ParsedSkill(
        name=frontmatter.name,
        description=frontmatter.description,
        body=body,
        frontmatter=frontmatter.model_dump(exclude={"metadata"}),
        files=files,
        content_hash=_content_hash(files),
        manifest={
            "name": frontmatter.name,
            "description": frontmatter.description,
            "license": frontmatter.license,
            "compatibility": frontmatter.compatibility,
            # The declared extension travels with the version so applicability
            # can be re-evaluated against the current metric registry instead of
            # being frozen at import time.
            "metric_keys": list(extension.metric_keys) if extension else [],
            "preferred_dimensions": list(extension.preferred_dimensions) if extension else [],
            "required_inputs": list(extension.required_inputs) if extension else [],
            "references": list(extension.references) if extension else [],
            "files": [
                {"path": item.path, "media_type": item.media_type, "sha256": item.sha256, "size": item.size}
                for item in sorted(files, key=lambda entry: entry.path)
            ],
        },
        metric_keys=tuple(extension.metric_keys) if extension else (),
        preferred_dimensions=tuple(extension.preferred_dimensions) if extension else (),
        required_inputs=tuple(extension.required_inputs) if extension else (),
        references=tuple(extension.references) if extension else (),
        display_metadata=display_metadata,
    )


def parse_skill_md(*, filename: str, data: bytes, settings: Settings) -> ParsedSkill:
    """A single `SKILL.md` upload (design 7.1)."""
    if filename.lower() != SKILL_FILENAME.lower():
        raise SkillError(
            ErrorCode.SKILL_UNSUPPORTED,
            "a single-file skill must be named SKILL.md (upload a ZIP for references)",
        )
    if len(data) > settings.skill_md_max_bytes:
        raise SkillError(ErrorCode.SKILL_TOO_LARGE, "SKILL.md exceeds its size limit")
    text = _normalize_text(data)
    item = PackageFile(
        path=SKILL_FILENAME,
        media_type="text/markdown",
        content=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        size=len(text.encode("utf-8")),
    )
    return _build_parsed_skill(skill_md=text, files=(item,), display_metadata={})


def parse_skill_zip(*, data: bytes, settings: Settings) -> ParsedSkill:
    """A ZIP bundle: `SKILL.md` plus optional `references/*.md|.txt` (design 7.1)."""
    if len(data) > settings.skill_upload_max_bytes:
        raise SkillError(ErrorCode.SKILL_TOO_LARGE, "the uploaded archive exceeds its size limit")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillError(ErrorCode.SKILL_INVALID, "the uploaded file is not a readable ZIP") from exc

    with archive:
        infos = archive.infolist()
        if any(info.flag_bits & 0x1 for info in infos):
            raise SkillError(ErrorCode.SKILL_UNSUPPORTED, "encrypted archives are not supported")
        members: list[tuple[zipfile.ZipInfo, str]] = []
        seen: set[str] = set()
        for info in infos:
            if info.is_dir():
                continue
            if _is_symlink(info):
                raise SkillError(ErrorCode.SKILL_UNSUPPORTED, "archives may not contain symlinks")
            path = _check_path(info.filename, settings=settings)
            if not path:
                continue
            if path.lower().endswith(ARCHIVE_SUFFIXES):
                raise SkillError(ErrorCode.SKILL_UNSUPPORTED, "nested archives are not supported")
            folded = path.casefold()
            if folded in seen:
                raise SkillError(ErrorCode.SKILL_UNSUPPORTED, f"duplicate path after normalization: {path!r}")
            seen.add(folded)
            members.append((info, path))

        paths = _strip_single_root([path for _, path in members])
        renamed = dict(zip([path for _, path in members], paths))
        if SKILL_FILENAME not in paths:
            raise SkillError(ErrorCode.SKILL_UNSUPPORTED, "the archive must contain SKILL.md at its root")
        if len(members) > settings.skill_files_max:
            raise SkillError(ErrorCode.SKILL_TOO_LARGE, "the archive contains too many files")

        files: list[PackageFile] = []
        expanded = 0
        for info, original in members:
            path = renamed[original]
            if path == SKILL_FILENAME:
                cap = settings.skill_md_max_bytes
            elif _is_reference(path) and path.lower().endswith(ALLOWED_REFERENCE_SUFFIXES):
                cap = settings.skill_file_max_bytes
            else:
                # Anything else - scripts/, other file types - rejects the whole
                # package instead of being silently dropped (design 7.1).
                raise SkillError(
                    ErrorCode.SKILL_UNSUPPORTED,
                    f"unsupported package entry: {path!r} (only SKILL.md and references/*.md|.txt are accepted)",
                )
            raw = _read_bounded(archive, info, cap=cap, settings=settings)
            text = _normalize_text(raw)
            expanded += len(text.encode("utf-8"))
            if expanded > settings.skill_expanded_max_bytes:
                raise SkillError(ErrorCode.SKILL_TOO_LARGE, "the expanded package exceeds its size limit")
            files.append(
                PackageFile(
                    path=path,
                    media_type=_media_type(path),
                    content=text,
                    sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    size=len(text.encode("utf-8")),
                )
            )

    skill_md = next(item.content for item in files if item.path == SKILL_FILENAME)
    return _build_parsed_skill(
        skill_md=skill_md, files=tuple(files), display_metadata={}
    )


def parse_skill_package(*, filename: str, data: bytes, settings: Settings) -> ParsedSkill:
    """Dispatch on the uploaded file name (design 7.1)."""
    lowered = (filename or "").lower()
    if lowered.endswith(".zip"):
        return parse_skill_zip(data=data, settings=settings)
    if lowered.endswith(".md"):
        return parse_skill_md(filename=filename, data=data, settings=settings)
    raise SkillError(
        ErrorCode.SKILL_UNSUPPORTED,
        "upload a SKILL.md file or a ZIP bundle containing SKILL.md",
    )
