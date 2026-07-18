"""Verify manifest-only environment lifecycle and safety."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

import skill_trivium.environment as environment_module
from skill_trivium.environment import (
    EnvironmentError,
    activate_environment,
    create_environment,
    deactivate_environment,
    describe_environment,
    environment_paths,
    list_environments,
    load_environment_state,
    load_runtime_snapshot,
    remove_environment,
    sync_active_environment,
)
from skill_trivium.lockfile import load_lockfile, write_lockfile
from skill_trivium.models import InstallContext, LockfileData, SkillLockEntry
from skill_trivium.skills import hash_skill_directory


def test_create_environment_writes_one_project_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Capture the current runtime directly into the project manifest directory."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    context = make_context(tmp_path / "project")
    write_runtime(context, {"alpha": "alpha"})

    record = create_environment(context, name="office")

    manifest_path = context.base_dir / ".agents" / "environments" / "office.lock"
    manifest = load_lockfile(manifest_path, expected_mode="project")
    assert record.skill_count == 1
    assert sorted(manifest.skills) == ["alpha"]
    assert manifest.meta["environment"] == "office"
    assert not (tmp_path / "home" / ".trivium" / "projects").exists()


def test_global_environment_can_activate_into_another_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use one global manifest in any current project runtime."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    first = make_context(tmp_path / "first")
    second = make_context(tmp_path / "second")
    write_runtime(first, {"alpha": "alpha"})
    create_environment(first, name="office", scope="global")
    write_runtime(second, {"beta": "beta"})
    install_fake_materializer(monkeypatch, {"alpha": "alpha", "beta": "beta"})

    activate_environment(second, "office", scope="global")

    assert (second.skills_dir / "alpha" / "SKILL.md").read_text(encoding="utf-8") == "alpha"
    assert not (second.skills_dir / "beta").exists()
    state = load_environment_state(second)
    assert state.active == "office"
    assert state.scope == "global"
    assert (tmp_path / "home" / ".trivium" / "environments" / "office.lock").is_file()
    assert not (second.base_dir / ".agents" / "environments" / "office.lock").exists()


def test_project_and_global_environments_with_same_name_are_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep project and global namespaces separate without fallback."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    context = make_context(tmp_path / "project")
    write_runtime(context, {"alpha": "alpha"})
    create_environment(context, name="office")
    create_environment(context, name="office", scope="global")

    assert [record.name for record in list_environments(context)] == ["office"]
    assert [record.name for record in list_environments(context, scope="global")] == ["office"]
    assert describe_environment(context, "office") is not None
    assert describe_environment(context, "office", scope="global") is not None


def test_active_environment_persists_runtime_changes_to_its_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Automatically update the one canonical manifest after runtime changes."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    context = make_context(tmp_path / "project")
    write_runtime(context, {"alpha": "alpha"})
    create_environment(context, name="office")
    install_fake_materializer(monkeypatch, {"alpha": "alpha", "beta": "beta"})
    activate_environment(context, "office")
    write_runtime(context, {"alpha": "alpha", "beta": "beta"})
    previous_revision = load_environment_state(context).revision

    sync_active_environment(context)

    manifest_path = environment_paths(context, scope="project").manifest_path("office")
    assert sorted(load_lockfile(manifest_path, expected_mode="project").skills) == ["alpha", "beta"]
    assert load_environment_state(context).revision != previous_revision


def test_stale_global_environment_rejects_automatic_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent a stale runtime from overwriting a global manifest changed elsewhere."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    context = make_context(tmp_path / "project")
    write_runtime(context, {"alpha": "alpha"})
    create_environment(context, name="office", scope="global")
    install_fake_materializer(monkeypatch, {"alpha": "alpha"})
    activate_environment(context, "office", scope="global")
    paths = environment_paths(context, scope="global")
    manifest = load_lockfile(paths.manifest_path("office"), expected_mode="global")
    manifest.meta["external_change"] = True
    environment_module._write_manifest(paths.manifest_path("office"), manifest, name="office", scope="global")

    with pytest.raises(EnvironmentError) as exc_info:
        sync_active_environment(context)

    assert exc_info.value.title == "Environment Changed Elsewhere"


def test_deactivate_restores_previous_runtime_from_its_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore the runtime captured immediately before activation."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    context = make_context(tmp_path / "project")
    write_runtime(context, {"alpha": "alpha"})
    create_environment(context, name="office")
    write_runtime(context, {"beta": "beta"})
    install_fake_materializer(monkeypatch, {"alpha": "alpha", "beta": "beta"})
    activate_environment(context, "office")

    assert deactivate_environment(context)

    assert (context.skills_dir / "beta" / "SKILL.md").read_text(encoding="utf-8") == "beta"
    assert not (context.skills_dir / "alpha").exists()
    assert load_environment_state(context).active is None


def test_deactivate_global_environment_after_manifest_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Always allow restoration even when a global manifest became stale."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    context = make_context(tmp_path / "project")
    write_runtime(context, {"alpha": "alpha"})
    create_environment(context, name="office", scope="global")
    write_runtime(context, {"beta": "beta"})
    install_fake_materializer(monkeypatch, {"alpha": "alpha", "beta": "beta"})
    activate_environment(context, "office", scope="global")
    paths = environment_paths(context, scope="global")
    manifest = load_lockfile(paths.manifest_path("office"), expected_mode="global")
    manifest.meta["external_change"] = True
    environment_module._write_manifest(paths.manifest_path("office"), manifest, name="office", scope="global")

    assert deactivate_environment(context)

    assert (context.skills_dir / "beta" / "SKILL.md").is_file()
    assert load_environment_state(context).active is None


def test_remove_rejects_active_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Require deactivation before deleting the canonical manifest."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    context = make_context(tmp_path / "project")
    write_runtime(context, {"alpha": "alpha"})
    create_environment(context, name="office")
    install_fake_materializer(monkeypatch, {"alpha": "alpha"})
    activate_environment(context, "office")

    with pytest.raises(EnvironmentError) as exc_info:
        remove_environment(context, "office")

    assert exc_info.value.title == "Environment Is Active"


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
    state: str,
    expected_title: str,
) -> None:
    """Reject runtime snapshots with missing, stale, or incomplete data."""
    context = make_context(tmp_path / "project")
    skill_dir = context.install_path_for("alpha")
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("alpha", encoding="utf-8")
    content_hash = None if state == "missing-hash" else "f" * 64
    write_lockfile(context, LockfileData(skills={"alpha": make_entry("alpha", content_hash)}))
    if state == "missing":
        environment_module.shutil.rmtree(skill_dir)

    with pytest.raises(EnvironmentError) as exc_info:
        load_runtime_snapshot(context)

    assert exc_info.value.title == expected_title


def test_environment_storage_rejects_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not follow a redirected canonical manifest directory."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    context = make_context(tmp_path / "project")
    write_runtime(context, {})
    target = tmp_path / "outside"
    target.mkdir()
    manifests_dir = context.base_dir / ".agents" / "environments"
    manifests_dir.parent.mkdir(parents=True, exist_ok=True)
    manifests_dir.symlink_to(target, target_is_directory=True)

    with pytest.raises(EnvironmentError) as exc_info:
        create_environment(context, name="office")

    assert exc_info.value.title == "Unsafe Environment Storage"


def test_environment_manifest_rejects_symlink(tmp_path: Path) -> None:
    """Reject an environment whose manifest file is itself a symlink."""
    context = make_context(tmp_path / "project")
    write_runtime(context, {})
    create_environment(context, name="office")
    manifest = environment_paths(context, scope="project").manifest_path("office")
    target = tmp_path / "redirected.lock"
    manifest.replace(target)
    manifest.symlink_to(target)

    with pytest.raises(EnvironmentError, match="regular file"):
        describe_environment(context, "office")


def make_context(project: Path) -> InstallContext:
    """Build a project installation context."""
    return InstallContext(
        mode="project",
        base_dir=project,
        skills_dir=project / ".agents" / "skills",
        lockfile_path=project / "skills.lock",
        install_prefix=Path(".agents/skills"),
    )


def write_runtime(context: InstallContext, skills: dict[str, str]) -> None:
    """Write a valid managed runtime containing simple skill documents."""
    if context.skills_dir.exists():
        environment_module.shutil.rmtree(context.skills_dir)
    context.skills_dir.mkdir(parents=True)
    entries = {}
    for name, content in skills.items():
        skill_dir = context.skills_dir / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        entries[name] = make_entry(name, hash_skill_directory(skill_dir))
    write_lockfile(context, LockfileData(skills=entries))


def make_entry(name: str, content_hash: str | None) -> SkillLockEntry:
    """Build a representative pinned manifest entry."""
    return SkillLockEntry(
        name=name,
        source_url="https://example.com/skills.git",
        commit_hash="a" * 40,
        content_hash=content_hash,
        skills_path="skills",
        install_path=f".agents/skills/{name}",
        description=name,
        installed_at="2026-01-01T00:00:00Z",
    )


def install_fake_materializer(monkeypatch: pytest.MonkeyPatch, contents: dict[str, str]) -> None:
    """Materialize test manifests without accessing remote Git repositories."""

    @contextmanager
    def materialized(manifest: LockfileData) -> Iterator[Path]:
        with TemporaryDirectory() as temp_dir_name:
            skills_dir = Path(temp_dir_name) / "skills"
            skills_dir.mkdir()
            for name in manifest.skills:
                skill_dir = skills_dir / name
                skill_dir.mkdir()
                (skill_dir / "SKILL.md").write_text(contents[name], encoding="utf-8")
            yield skills_dir

    monkeypatch.setattr(environment_module, "_materialized_environment", materialized)
