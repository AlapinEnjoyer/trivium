from pathlib import Path

import pytest

from skill_trivium.models import InstallContext
from skill_trivium.skills import (
    discover_skills_path,
    hash_parsed_skill,
    hash_skill_directory,
    install_skill_tree,
    parse_skill_document,
    validate_skill_directory,
    validate_skill_name,
)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("name: alpha\n", "must start with YAML frontmatter"),
        ("---\nname: alpha\n", "missing a closing"),
        ("---\nname: [\n---\n", "Invalid YAML frontmatter"),
        ("---\n- alpha\n---\n", "must be a YAML mapping"),
    ],
)
def test_parse_skill_document_rejects_invalid_frontmatter(tmp_path: Path, content: str, message: str) -> None:
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        parse_skill_document(skill_file)


def test_parse_skill_document_removes_crlf_separator_before_body(tmp_path: Path) -> None:
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_bytes(b"---\r\nname: alpha\r\ndescription: Alpha\r\n---\r\n\r\n## Instructions\r\n")

    frontmatter, body = parse_skill_document(skill_file)

    assert frontmatter["name"] == "alpha"
    assert body == "## Instructions\n"


def test_discover_skills_path_prefers_skills_directory_and_rejects_escape(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested_skill = repo / "skills" / "nested"
    root_skill = repo / "root"
    outside_skill = tmp_path / "outside" / "escaped"
    for skill_dir in (nested_skill, root_skill, outside_skill):
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("", encoding="utf-8")

    assert discover_skills_path(repo, None) == (repo / "skills", "skills")
    assert discover_skills_path(repo, "../outside") is None


@pytest.mark.parametrize("name", ["a", "a" * 64, "alpha-2"])
def test_validate_skill_name_accepts_boundary_values(name: str) -> None:
    assert validate_skill_name(name, skill_name=name, expected_directory=name) == []


@pytest.mark.parametrize("name", ["", "a" * 65, "Bad", "-bad", "bad-", "bad--name"])
def test_validate_skill_name_rejects_invalid_values(name: str) -> None:
    assert validate_skill_name(name, skill_name=name)


def test_install_skill_tree_hashes_normalized_content_and_removes_stale_files(tmp_path: Path) -> None:
    source = tmp_path / "source" / "alpha"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: ' Alpha skill '\n---\n\n## Instructions\n", encoding="utf-8"
    )
    (source / "asset.txt").write_text("current", encoding="utf-8")
    parsed, issues = validate_skill_directory(source)
    assert issues == []
    assert parsed is not None

    destination = tmp_path / "installed" / "alpha"
    destination.mkdir(parents=True)
    (destination / "stale.txt").write_text("stale", encoding="utf-8")

    install_skill_tree(parsed, destination)

    assert not (destination / "stale.txt").exists()
    assert (destination / "asset.txt").read_text(encoding="utf-8") == "current"
    assert hash_skill_directory(destination) == hash_parsed_skill(parsed)


@pytest.mark.parametrize("unsafe_name", ["../escape", "/tmp/escape", ".", "..", ""])
def test_install_context_rejects_unsafe_skill_paths(tmp_path: Path, unsafe_name: str) -> None:
    context = InstallContext(
        mode="project",
        base_dir=tmp_path,
        skills_dir=tmp_path / ".agents" / "skills",
        lockfile_path=tmp_path / "skills.lock",
        install_prefix=Path(".agents/skills"),
    )

    with pytest.raises(ValueError, match="single relative path component"):
        context.install_path_for(unsafe_name)
    with pytest.raises(ValueError, match="single relative path component"):
        context.relative_install_path(unsafe_name)
