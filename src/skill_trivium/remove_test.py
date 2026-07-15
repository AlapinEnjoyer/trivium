"""Verify serialized skill removal and stale-state handling."""

from pathlib import Path

import pytest

import skill_trivium.remove as remove_module
from skill_trivium.lockfile import load_lockfile, write_lockfile
from skill_trivium.models import InstallContext, LockfileData, SkillLockEntry
from skill_trivium.remove import run_remove


def test_run_remove_updates_runtime_lockfile_and_active_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apply all removal side effects inside the workflow boundary."""
    context = _make_context(tmp_path)
    skill_dir = context.install_path_for("alpha")
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("alpha", encoding="utf-8")
    write_lockfile(context, LockfileData(skills={"alpha": _make_entry("alpha")}))
    synchronized: list[InstallContext] = []
    monkeypatch.setattr(remove_module, "sync_active_environment", synchronized.append)

    outcome = run_remove(context, ["alpha"])

    assert outcome.removed == ("alpha",)
    assert outcome.missing == ()
    assert not skill_dir.exists()
    assert load_lockfile(context.lockfile_path, expected_mode="project").skills == {}
    assert synchronized == [context]


def test_run_remove_does_not_mutate_when_locked_state_is_missing_a_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Return stale requested names without partially removing other skills."""
    context = _make_context(tmp_path)
    skill_dir = context.install_path_for("alpha")
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("alpha", encoding="utf-8")
    write_lockfile(context, LockfileData(skills={"alpha": _make_entry("alpha")}))
    synchronized: list[InstallContext] = []
    monkeypatch.setattr(remove_module, "sync_active_environment", synchronized.append)

    outcome = run_remove(context, ["alpha", "beta"])

    assert outcome.removed == ()
    assert outcome.missing == ("beta",)
    assert skill_dir.is_dir()
    assert sorted(load_lockfile(context.lockfile_path, expected_mode="project").skills) == ["alpha"]
    assert synchronized == []


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
        commit_hash="abc123",
        content_hash="hash",
        skills_path="skills",
        install_path=f".agents/skills/{name}",
        description=name,
        installed_at="2026-01-01T00:00:00Z",
    )
