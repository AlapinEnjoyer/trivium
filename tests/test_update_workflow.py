"""Verify update workflows and runtime restoration."""

from pathlib import Path

import pytest

import skill_trivium.update as update_module
from skill_trivium.environment import EnvironmentError
from skill_trivium.lockfile import load_lockfile, write_lockfile
from skill_trivium.models import InstallContext, LockfileData, SkillLockEntry, SourceUpdateResult
from skill_trivium.update import run_update


def test_run_update_restores_runtime_when_lockfile_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore updated skill trees when the lockfile cannot be committed."""
    context = _make_context(tmp_path)
    skill_dir = context.install_path_for("alpha")
    skill_dir.mkdir(parents=True)
    installed_document = skill_dir / "SKILL.md"
    installed_document.write_text("previous", encoding="utf-8")
    original_entry = _make_entry("alpha")
    write_lockfile(context, LockfileData(skills={"alpha": original_entry}))
    updated_entry = _make_entry("alpha")
    updated_entry.commit_hash = "updated"

    def update_source(
        _entries: list[SkillLockEntry],
        update_context: InstallContext,
        _dry_run: bool,
    ) -> SourceUpdateResult:
        update_context.install_path_for("alpha").joinpath("SKILL.md").write_text("updated", encoding="utf-8")
        return SourceUpdateResult(updated={"alpha": updated_entry})

    def failed_write(_context: InstallContext, _lockfile: LockfileData) -> None:
        raise OSError("simulated lockfile failure")

    monkeypatch.setattr(update_module, "_update_source_group", update_source)
    monkeypatch.setattr(update_module, "write_lockfile", failed_write)

    with pytest.raises(OSError, match="simulated lockfile failure"):
        run_update(context=context, requested_skills=[], dry_run=False)

    assert installed_document.read_text(encoding="utf-8") == "previous"
    assert load_lockfile(context.lockfile_path, expected_mode="project").skills["alpha"].commit_hash == "a" * 7


def test_run_update_restores_runtime_when_environment_sync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore updated runtime artifacts when environment synchronization fails."""
    context = _make_context(tmp_path)
    skill_dir = context.install_path_for("alpha")
    skill_dir.mkdir(parents=True)
    installed_document = skill_dir / "SKILL.md"
    installed_document.write_text("previous", encoding="utf-8")
    write_lockfile(context, LockfileData(skills={"alpha": _make_entry("alpha")}))
    updated_entry = _make_entry("alpha")
    updated_entry.commit_hash = "updated"

    def update_source(
        _entries: list[SkillLockEntry],
        update_context: InstallContext,
        _dry_run: bool,
    ) -> SourceUpdateResult:
        update_context.install_path_for("alpha").joinpath("SKILL.md").write_text("updated", encoding="utf-8")
        return SourceUpdateResult(updated={"alpha": updated_entry})

    def failed_sync(_context: InstallContext) -> None:
        raise EnvironmentError(title="Sync Failed", lines=("simulated sync failure",))

    monkeypatch.setattr(update_module, "_update_source_group", update_source)
    monkeypatch.setattr(update_module, "sync_active_environment", failed_sync)

    with pytest.raises(EnvironmentError, match="simulated sync failure"):
        run_update(context=context, requested_skills=[], dry_run=False)

    assert installed_document.read_text(encoding="utf-8") == "previous"
    assert load_lockfile(context.lockfile_path, expected_mode="project").skills["alpha"].commit_hash == "a" * 7


def _make_context(tmp_path: Path) -> InstallContext:
    return InstallContext(
        mode="project",
        base_dir=tmp_path,
        skills_dir=tmp_path / ".agents" / "skills",
        lockfile_path=tmp_path / "skills.lock",
        install_prefix=Path(".agents/skills"),
    )


def _make_entry(name: str) -> SkillLockEntry:
    return SkillLockEntry(
        name=name,
        source_url="https://example.com/skills.git",
        commit_hash="a" * 7,
        content_hash="b" * 64,
        skills_path="skills",
        install_path=f".agents/skills/{name}",
        description=name,
        installed_at="2026-01-01T00:00:00Z",
    )
