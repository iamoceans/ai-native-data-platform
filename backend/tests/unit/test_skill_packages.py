"""Skill package parsing: formats, limits and deterministic hashing (design 7)."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.config import get_settings
from app.constants import ErrorCode
from app.skills.frontmatter import SkillError
from app.skills.package import parse_skill_md, parse_skill_package, parse_skill_zip

BODY = "# 收入变化分析\n先明确期间、时区和指标口径。\n"

FRONT_MATTER = """---
name: revenue-diagnosis
description: 分析收入变化，优先核实口径、完整性和渠道贡献。
license: Apache-2.0
compatibility: 依赖 ads_revenue 指标与既有贡献分解。
metadata:
  askdata:
    schema_version: 1
    metric_keys: [ads_revenue]
    preferred_dimensions: [platform, ad_network]
    required_inputs: [metric_key, baseline_period, current_period]
{extra}---
"""


def skill_md(extra: str = "", body: str = BODY) -> str:
    return FRONT_MATTER.format(extra=extra) + body


def zip_bytes(entries: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def settings():
    return get_settings()


def test_single_skill_md_parses_with_its_extension():
    parsed = parse_skill_md(filename="SKILL.md", data=skill_md().encode(), settings=settings())
    assert parsed.name == "revenue-diagnosis"
    assert parsed.metric_keys == ("ads_revenue",)
    assert parsed.preferred_dimensions == ("platform", "ad_network")
    assert parsed.required_inputs == ("metric_key", "baseline_period", "current_period")
    assert [item.path for item in parsed.files] == ["SKILL.md"]
    assert parsed.manifest["license"] == "Apache-2.0"


def test_zip_keeps_skill_md_and_references_and_strips_one_root_directory():
    parsed = parse_skill_zip(
        data=zip_bytes(
            {
                "revenue-diagnosis/SKILL.md": skill_md(extra="    references: [references/notes.md]\n"),
                "revenue-diagnosis/references/notes.md": "口径说明\n",
            }
        ),
        settings=settings(),
    )
    assert [item.path for item in parsed.files] == ["SKILL.md", "references/notes.md"]
    assert parsed.files[1].media_type == "text/markdown"
    assert parsed.references == ("references/notes.md",)


def test_hash_ignores_bom_and_line_endings_but_not_content():
    base = parse_skill_md(filename="SKILL.md", data=skill_md().encode(), settings=settings())
    crlf = parse_skill_md(
        filename="SKILL.md", data=skill_md().replace("\n", "\r\n").encode(), settings=settings()
    )
    bom = parse_skill_md(
        filename="SKILL.md", data=b"\xef\xbb\xbf" + skill_md().encode(), settings=settings()
    )
    changed = parse_skill_md(
        filename="SKILL.md", data=skill_md(body=BODY + "补充一行。\n").encode(), settings=settings()
    )
    assert base.content_hash == crlf.content_hash == bom.content_hash
    assert base.content_hash != changed.content_hash


def test_a_single_file_may_not_declare_references():
    with pytest.raises(SkillError) as caught:
        parse_skill_md(
            filename="SKILL.md",
            data=skill_md(extra="    references: [references/notes.md]\n").encode(),
            settings=settings(),
        )
    assert caught.value.code == ErrorCode.SKILL_INVALID
    assert "not in the package" in caught.value.message


def test_dispatch_rejects_unknown_file_types():
    with pytest.raises(SkillError) as caught:
        parse_skill_package(filename="skill.txt", data=b"x", settings=settings())
    assert caught.value.code == ErrorCode.SKILL_UNSUPPORTED


def test_single_file_must_be_named_skill_md():
    with pytest.raises(SkillError) as caught:
        parse_skill_package(filename="notes.md", data=skill_md().encode(), settings=settings())
    assert caught.value.code == ErrorCode.SKILL_UNSUPPORTED


def test_oversized_skill_md_is_rejected():
    big = skill_md(body=BODY + ("字" * 40_000))
    with pytest.raises(SkillError) as caught:
        parse_skill_md(filename="SKILL.md", data=big.encode(), settings=settings())
    assert caught.value.code == ErrorCode.SKILL_TOO_LARGE


def test_zip_must_contain_skill_md_at_its_root():
    with pytest.raises(SkillError) as caught:
        parse_skill_zip(
            data=zip_bytes({"references/notes.md": "x"}), settings=settings()
        )
    assert caught.value.code == ErrorCode.SKILL_UNSUPPORTED


def test_too_many_files_is_rejected():
    entries = {"SKILL.md": skill_md()}
    for index in range(40):
        entries[f"references/note-{index}.md"] = "x"
    with pytest.raises(SkillError) as caught:
        parse_skill_zip(data=zip_bytes(entries), settings=settings())
    assert caught.value.code == ErrorCode.SKILL_TOO_LARGE


def test_non_utf8_content_is_rejected():
    with pytest.raises(SkillError) as caught:
        parse_skill_md(filename="SKILL.md", data=b"\xff\xfe\x00bad", settings=settings())
    assert caught.value.code == ErrorCode.SKILL_INVALID
