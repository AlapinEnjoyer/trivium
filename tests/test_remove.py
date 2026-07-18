"""Verify serialized skill removal and stale-state handling."""

from pathlib import Path

import pytest

import skill_trivium.remove as remove_module
from skill_trivium.environment import EnvironmentError
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


def test_run_remove_restores_runtime_when_lockfile_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore removed skill trees when the lockfile cannot be committed."""
    context = _make_context(tmp_path)
    skill_dir = context.install_path_for("alpha")
    skill_dir.mkdir(parents=True)
    installed_document = skill_dir / "SKILL.md"
    installed_document.write_text("alpha", encoding="utf-8")
    write_lockfile(context, LockfileData(skills={"alpha": _make_entry("alpha")}))

    def failed_write(_context: InstallContext, _lockfile: LockfileData) -> None:
        raise OSError("simulated lockfile failure")

    monkeypatch.setattr(remove_module, "write_lockfile", failed_write)

    with pytest.raises(OSError, match="simulated lockfile failure"):
        run_remove(context, ["alpha"])

    assert installed_document.read_text(encoding="utf-8") == "alpha"
    assert sorted(load_lockfile(context.lockfile_path, expected_mode="project").skills) == ["alpha"]


def test_run_remove_restores_runtime_when_environment_sync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Roll back the runtime and lockfile when environment synchronization fails."""
    context = _make_context(tmp_path)
    skill_dir = context.install_path_for("alpha")
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("alpha", encoding="utf-8")
    write_lockfile(context, LockfileData(skills={"alpha": _make_entry("alpha")}))

    def failed_sync(_context: InstallContext) -> None:
        raise EnvironmentError(title="Sync Failed", lines=("simulated sync failure",))

    monkeypatch.setattr(remove_module, "sync_active_environment", failed_sync)

    with pytest.raises(EnvironmentError, match="simulated sync failure"):
        run_remove(context, ["alpha"])

    assert skill_dir.is_dir()
    assert sorted(load_lockfile(context.lockfile_path, expected_mode="project").skills) == ["alpha"]


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
