"""Verify environment state persistence and runtime snapshot safeguards.

The cases ensure environment operations reject missing, incomplete, or dirty
installations without accidentally clearing the active-environment state.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import skill_trivium.environment as environment_module
from skill_trivium.environment import (
    EnvironmentError,
    EnvironmentState,
    activate_environment,
    create_environment,
    deactivate_environment,
    environment_paths,
    load_environment_state,
    load_runtime_snapshot,
    remove_environment,
    sync_active_environment,
    write_environment_state,
)
from skill_trivium.lockfile import LockfileError, write_lockfile
from skill_trivium.models import InstallContext, LockfileData, SkillLockEntry


def make_context(tmp_path: Path) -> InstallContext:
    """Build a project installation context under the test directory."""
    project = tmp_path / "project"
    return InstallContext(
        mode="project",
        base_dir=project,
        skills_dir=project / ".agents" / "skills",
        lockfile_path=project / "skills.lock",
        install_prefix=Path(".agents/skills"),
    )


def make_entry(*, content_hash: str | None) -> SkillLockEntry:
    """Build a representative lockfile entry."""
    return SkillLockEntry(
        name="alpha",
        source_url="https://git.example.com/repo.git",
        commit_hash="abc123",
        content_hash=content_hash,
        skills_path="skills",
        install_path=".agents/skills/alpha",
        description="Alpha",
        installed_at="2026-01-01T00:00:00Z",
    )


@pytest.mark.parametrize(
    ("state", "expected_title"),
    [
        ("missing", "Missing Installed Skills"),
        ("missing-hash", "Lockfile Missing Content Hashes"),
        ("dirty", "Runtime Has Local Modifications"),
    ],
)
def test_load_runtime_snapshot_rejects_inconsistent_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    expected_title: str,
) -> None:
    """Reject runtime snapshots with missing, stale, or incomplete data."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    context = make_context(tmp_path)
    content_hash = None if state == "missing-hash" else "incorrect-hash"
    write_lockfile(context, LockfileData(skills={"alpha": make_entry(content_hash=content_hash)}))
    if state != "missing":
        skill_dir = context.install_path_for("alpha")
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("alpha", encoding="utf-8")

    with pytest.raises(EnvironmentError) as exc_info:
        load_runtime_snapshot(context, require_content_hashes=True)

    assert exc_info.value.title == expected_title
    assert exc_info.value.exit_code == 2
    assert "alpha" in str(exc_info.value)


def test_sync_active_environment_reports_missing_snapshot_without_clearing_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve active state when its snapshot is missing."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    context = make_context(tmp_path)
    write_environment_state(context, EnvironmentState(active="office"))

    with pytest.raises(EnvironmentError) as exc_info:
        sync_active_environment(context)

    assert exc_info.value.title == "Missing Environment Snapshot"
    assert load_environment_state(context).active == "office"


def test_activate_environment_reloads_state_after_acquiring_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observe an environment activated while waiting for the mutation lock."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    context = make_context(tmp_path)

    @contextmanager
    def activate_while_locking(locked_context: InstallContext) -> Iterator[None]:
        assert locked_context is context
        write_environment_state(context, EnvironmentState(active="office"))
        yield

    monkeypatch.setattr(environment_module, "installation_lock", activate_while_locking)

    with pytest.raises(EnvironmentError) as exc_info:
        activate_environment(context, "office")

    assert exc_info.value.title == "Environment Already Active"
    assert load_environment_state(context).active == "office"


def test_deactivate_environment_reloads_state_after_acquiring_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observe an environment deactivated while waiting for the mutation lock."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    context = make_context(tmp_path)
    write_environment_state(context, EnvironmentState(active="office"))

    @contextmanager
    def deactivate_while_locking(locked_context: InstallContext) -> Iterator[None]:
        assert locked_context is context
        write_environment_state(context, EnvironmentState())
        yield

    monkeypatch.setattr(environment_module, "installation_lock", deactivate_while_locking)

    was_active = deactivate_environment(context)

    assert not was_active
    assert load_environment_state(context).active is None


def test_remove_environment_reloads_snapshot_state_after_acquiring_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject removal when the environment disappears while waiting for the lock."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    context = make_context(tmp_path)
    environment_dir = environment_paths(context, scope="project").env_dir("office")
    environment_dir.mkdir(parents=True)

    @contextmanager
    def remove_while_locking(locked_context: InstallContext) -> Iterator[None]:
        assert locked_context is context
        environment_dir.rmdir()
        yield

    monkeypatch.setattr(environment_module, "installation_lock", remove_while_locking)

    with pytest.raises(EnvironmentError) as exc_info:
        remove_environment(context, "office")

    assert exc_info.value.title == "Environment Not Found"


def test_create_environment_reloads_conflicts_after_acquiring_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject creation when the environment appears while waiting for the lock."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    context = make_context(tmp_path)
    environment_dir = environment_paths(context, scope="project").env_dir("office")

    @contextmanager
    def create_while_locking(locked_context: InstallContext) -> Iterator[None]:
        assert locked_context is context
        environment_dir.mkdir(parents=True)
        yield

    monkeypatch.setattr(environment_module, "installation_lock", create_while_locking)

    with pytest.raises(EnvironmentError) as exc_info:
        create_environment(context, name="office", empty=True, shared=False)

    assert exc_info.value.title == "Environment Exists"


def test_restore_snapshot_validates_lockfile_before_replacing_runtime(tmp_path: Path) -> None:
    """Keep the current runtime when a replacement snapshot is malformed."""
    context = make_context(tmp_path)
    installed_skill = context.install_path_for("alpha")
    installed_skill.mkdir(parents=True)
    installed_document = installed_skill / "SKILL.md"
    installed_document.write_text("original runtime", encoding="utf-8")

    snapshot_dir = tmp_path / "snapshot"
    snapshot_skill = snapshot_dir / "skills" / "alpha"
    snapshot_skill.mkdir(parents=True)
    (snapshot_skill / "SKILL.md").write_text("replacement runtime", encoding="utf-8")
    (snapshot_dir / "skills.lock").write_text("[invalid", encoding="utf-8")

    with pytest.raises(LockfileError):
        environment_module._restore_snapshot(snapshot_dir, context)

    assert installed_document.read_text(encoding="utf-8") == "original runtime"


def test_restore_snapshot_rolls_back_runtime_when_lockfile_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore the previous skill tree when committing a snapshot fails."""
    context = make_context(tmp_path)
    installed_skill = context.install_path_for("alpha")
    installed_skill.mkdir(parents=True)
    installed_document = installed_skill / "SKILL.md"
    installed_document.write_text("original runtime", encoding="utf-8")

    snapshot_dir = tmp_path / "snapshot"
    snapshot_skill = snapshot_dir / "skills" / "alpha"
    snapshot_skill.mkdir(parents=True)
    (snapshot_skill / "SKILL.md").write_text("replacement runtime", encoding="utf-8")
    write_lockfile(
        InstallContext(
            mode="project",
            base_dir=snapshot_dir,
            skills_dir=snapshot_dir / "skills",
            lockfile_path=snapshot_dir / "skills.lock",
            install_prefix=Path("skills"),
        ),
        LockfileData(),
    )

    def failed_write(_context: InstallContext, _lockfile: LockfileData) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(environment_module, "write_lockfile", failed_write)

    with pytest.raises(OSError, match="simulated write failure"):
        environment_module._restore_snapshot(snapshot_dir, context)

    assert installed_document.read_text(encoding="utf-8") == "original runtime"
