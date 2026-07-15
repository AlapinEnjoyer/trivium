"""Verify add runtime recovery before lockfile commit."""

from pathlib import Path

import pytest

import skill_trivium.add as add_module
from skill_trivium.lockfile import load_lockfile, write_lockfile
from skill_trivium.models import InstallContext, LockfileData, SkillLockEntry
from skill_trivium.skills import validate_skill_directory


def test_pending_add_restores_runtime_when_lockfile_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore replaced skills when the add lockfile cannot be committed."""
    context = _make_context(tmp_path)
    installed_skill = context.install_path_for("alpha")
    installed_skill.mkdir(parents=True)
    installed_document = installed_skill / "SKILL.md"
    installed_document.write_text("previous", encoding="utf-8")
    write_lockfile(context, LockfileData(skills={"alpha": _make_entry()}))
    source_skill = tmp_path / "source" / "alpha"
    source_skill.mkdir(parents=True)
    (source_skill / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Updated alpha\n---\n",
        encoding="utf-8",
    )
    parsed_skill, issues = validate_skill_directory(source_skill)
    assert issues == []
    assert parsed_skill is not None

    def failed_write(_context: InstallContext, _lockfile: LockfileData) -> None:
        raise OSError("simulated lockfile failure")

    monkeypatch.setattr(add_module, "write_lockfile", failed_write)

    with pytest.raises(OSError, match="simulated lockfile failure"):
        add_module._apply_pending_installs(
            pending_installs=[parsed_skill],
            source_url="https://example.com/updated.git",
            commit_hash="updated",
            skills_path="skills",
            context=context,
            lockfile=load_lockfile(context.lockfile_path, expected_mode="project"),
            dry_run=False,
            installed=[],
            would_install=[],
        )

    assert installed_document.read_text(encoding="utf-8") == "previous"
    assert load_lockfile(context.lockfile_path, expected_mode="project").skills["alpha"].commit_hash == "abc123"


def _make_context(tmp_path: Path) -> InstallContext:
    return InstallContext(
        mode="project",
        base_dir=tmp_path,
        skills_dir=tmp_path / ".agents" / "skills",
        lockfile_path=tmp_path / "skills.lock",
        install_prefix=Path(".agents/skills"),
    )


def _make_entry() -> SkillLockEntry:
    return SkillLockEntry(
        name="alpha",
        source_url="https://example.com/original.git",
        commit_hash="abc123",
        content_hash="hash",
        skills_path="skills",
        install_path=".agents/skills/alpha",
        description="Alpha",
        installed_at="2026-01-01T00:00:00Z",
    )
