"""Verify update locking and post-lock state loading."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import skill_trivium.update as update_module
from skill_trivium.lockfile import write_lockfile
from skill_trivium.models import InstallContext, LockfileData, SkillLockEntry
from skill_trivium.update import run_update


def test_run_update_reloads_state_after_acquiring_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject a requested skill removed while waiting for the mutation lock."""
    context = _make_context(tmp_path)
    write_lockfile(context, LockfileData(skills={"alpha": _make_entry("alpha")}))

    @contextmanager
    def replace_state_while_locking(_context: InstallContext) -> Iterator[None]:
        write_lockfile(context, LockfileData(skills={"beta": _make_entry("beta")}))
        yield

    monkeypatch.setattr(update_module, "installation_lock", replace_state_while_locking)

    outcome = run_update(context=context, requested_skills=["alpha"], dry_run=False)

    assert outcome.missing_names == ("alpha",)
    assert not outcome.runtime_changed
    assert not outcome.lockfile_changed


def test_run_update_dry_run_does_not_acquire_mutation_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep read-only update previews outside the mutation lock."""
    context = _make_context(tmp_path)

    def fail_if_locked(_context: InstallContext) -> None:
        raise AssertionError("dry-run update acquired the installation lock")

    monkeypatch.setattr(update_module, "installation_lock", fail_if_locked)

    outcome = run_update(context=context, requested_skills=[], dry_run=True)

    assert outcome.nothing_installed


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
