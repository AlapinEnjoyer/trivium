"""Manage project and global environment manifests."""

import os
import shutil
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Literal

import tomli_w

from skill_trivium.git import GitCheckoutError, GitCloneError, cloned_repo_at_revision
from skill_trivium.lockfile import (
    LOCKFILE_VERSION,
    exclusive_file_lock,
    installation_lock,
    load_lockfile,
    write_lockfile_path,
)
from skill_trivium.models import InstallContext, LockfileData, SkillLockEntry
from skill_trivium.mutation import RuntimeMutation
from skill_trivium.skills import (
    MAX_NAME_LENGTH,
    NAME_PATTERN,
    hash_skill_directory,
    install_skill_tree,
    render_skill_document,
    resolve_repo_path,
    utc_now,
    validate_skill_directory,
)

TRIVIUM_HOME_DIR = ".trivium"
ENVIRONMENTS_DIR = "environments"
SESSIONS_DIR = "sessions"
STATE_FILE = "state.toml"
PREVIOUS_LOCKFILE = "previous.lock"
EnvironmentScope = Literal["project", "global"]


@dataclass(slots=True)
class EnvironmentError(Exception):
    """Describe an environment operation failure for CLI rendering."""

    title: str
    lines: tuple[str, ...]
    exit_code: int = 1
    kind: str = "err"

    def __str__(self) -> str:
        """Return the environment error messages as one string."""
        return " ".join(self.lines)


@dataclass(frozen=True, slots=True)
class EnvironmentPaths:
    """Store canonical manifest and runtime-session paths."""

    manifests_dir: Path
    state_path: Path
    previous_lockfile_path: Path
    lock_path: Path

    def manifest_path(self, name: str) -> Path:
        """Return the canonical manifest path for an environment name."""
        _validate_or_raise(name)
        return self.manifests_dir / f"{name}.lock"


@dataclass(frozen=True, slots=True)
class EnvironmentState:
    """Identify the active manifest and the runtime it replaced."""

    active: str | None = None
    scope: EnvironmentScope | None = None
    revision: str | None = None
    previous_lockfile_present: bool = False


@dataclass(frozen=True, slots=True)
class EnvironmentRecord:
    """Summarize one environment manifest."""

    name: str
    active: bool
    skill_count: int


@dataclass(frozen=True, slots=True)
class EnvironmentDetails:
    """Describe one environment manifest."""

    name: str
    scope: EnvironmentScope
    active: bool
    skill_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """Represent a verified runtime lockfile and skill tree."""

    lockfile: LockfileData
    lockfile_present: bool


def validate_environment_name(name: str) -> str | None:
    """Return a validation error for an environment name, if invalid."""
    if not 1 <= len(name) <= MAX_NAME_LENGTH:
        return f"Environment names must be between 1 and {MAX_NAME_LENGTH} characters."
    if name.strip() != name or any(character.isupper() for character in name):
        return "Environment names may only use lowercase letters, digits, and single hyphens."
    if NAME_PATTERN.fullmatch(name) is None:
        return "Environment names may only use lowercase letters, digits, and single hyphens."
    return None


def environment_paths(context: InstallContext, *, scope: EnvironmentScope) -> EnvironmentPaths:
    """Resolve canonical manifests and transient state for a runtime."""
    home = Path.home() / TRIVIUM_HOME_DIR
    manifests_dir = home / ENVIRONMENTS_DIR if scope == "global" else context.base_dir / ".agents" / ENVIRONMENTS_DIR
    session_dir = home / SESSIONS_DIR / sha256(context.base_dir.resolve().as_posix().encode()).hexdigest()
    return EnvironmentPaths(
        manifests_dir=manifests_dir,
        state_path=session_dir / STATE_FILE,
        previous_lockfile_path=session_dir / PREVIOUS_LOCKFILE,
        lock_path=manifests_dir.parent / f".{ENVIRONMENTS_DIR}.lock",
    )


def active_environment_name(context: InstallContext) -> str | None:
    """Return the active environment name for a runtime."""
    return load_environment_state(context).active


def load_environment_state(context: InstallContext) -> EnvironmentState:
    """Load the active environment session for a runtime."""
    state_path = environment_paths(context, scope="project").state_path
    if state_path.is_symlink():
        raise EnvironmentError(
            title="Invalid Environment State",
            lines=(f"The environment state file '{state_path}' must not be a symbolic link.",),
            exit_code=2,
        )
    if not state_path.exists():
        return EnvironmentState()
    try:
        with state_path.open("rb") as file:
            payload = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise EnvironmentError(
            title="Invalid Environment State",
            lines=(f"The environment state file '{state_path}' is malformed.",),
            exit_code=2,
        ) from error

    active = payload.get("active")
    scope = payload.get("scope")
    revision = payload.get("revision")
    previous_present = payload.get("previous_lockfile_present")
    if (
        not isinstance(active, str)
        or validate_environment_name(active) is not None
        or scope not in {"project", "global"}
        or not isinstance(revision, str)
        or len(revision) != 64
        or type(previous_present) is not bool
    ):
        raise EnvironmentError(
            title="Invalid Environment State",
            lines=(f"The environment state file '{state_path}' contains invalid values.",),
            exit_code=2,
        )
    return EnvironmentState(
        active=active,
        scope=scope,
        revision=revision,
        previous_lockfile_present=previous_present,
    )


def write_environment_state(context: InstallContext, state: EnvironmentState) -> None:
    """Persist or clear the active environment session."""
    state_path = environment_paths(context, scope="project").state_path
    if state.active is None:
        state_path.unlink(missing_ok=True)
        return
    if state.scope is None or state.revision is None:
        raise ValueError("Active environment state requires scope and revision.")
    _validate_or_raise(state.active)
    _write_bytes(
        state_path,
        tomli_w.dumps(
            {
                "active": state.active,
                "scope": state.scope,
                "revision": state.revision,
                "previous_lockfile_present": state.previous_lockfile_present,
            }
        ).encode(),
    )


def list_environments(
    context: InstallContext,
    *,
    scope: EnvironmentScope = "project",
) -> list[EnvironmentRecord]:
    """List environment manifests in one explicit scope."""
    paths = environment_paths(context, scope=scope)
    _validate_manifest_storage(paths)
    state = load_environment_state(context)
    if not paths.manifests_dir.is_dir():
        return []
    records = []
    for manifest_path in sorted(paths.manifests_dir.glob("*.lock")):
        if not manifest_path.is_file() or manifest_path.is_symlink():
            continue
        name = manifest_path.stem
        if validate_environment_name(name) is not None:
            continue
        manifest = _load_manifest(paths, name, scope)
        records.append(
            EnvironmentRecord(
                name=name,
                active=state.active == name and state.scope == scope,
                skill_count=len(manifest.skills),
            )
        )
    return records


def describe_environment(
    context: InstallContext,
    name: str | None = None,
    *,
    scope: EnvironmentScope = "project",
) -> EnvironmentDetails | None:
    """Return details for a named or active environment manifest."""
    state = load_environment_state(context)
    if name is None and state.active is not None and state.scope is not None:
        target_name = state.active
        scope = state.scope
    else:
        target_name = name
    if target_name is None:
        return None
    _validate_or_raise(target_name)
    paths = environment_paths(context, scope=scope)
    manifest_path = paths.manifest_path(target_name)
    if not manifest_path.exists():
        return None
    manifest = _load_manifest(paths, target_name, scope)
    return EnvironmentDetails(
        name=target_name,
        scope=scope,
        active=state.active == target_name and state.scope == scope,
        skill_names=tuple(sorted(manifest.skills)),
    )


def create_environment(
    context: InstallContext,
    *,
    name: str,
    scope: EnvironmentScope = "project",
) -> EnvironmentRecord:
    """Capture the verified current runtime as a canonical manifest."""
    _validate_or_raise(name)
    with installation_lock(context), _scope_lock(environment_paths(context, scope=scope), scope):
        snapshot = load_runtime_snapshot(context)
        paths = environment_paths(context, scope=scope)
        _validate_manifest_storage(paths)
        manifest_path = paths.manifest_path(name)
        if manifest_path.exists() or manifest_path.is_symlink():
            raise EnvironmentError(
                title="Environment Exists",
                lines=(f"The {scope} environment '{name}' already exists.",),
            )
        _write_manifest(manifest_path, snapshot.lockfile, name=name, scope=scope)
        return EnvironmentRecord(name=name, active=False, skill_count=len(snapshot.lockfile.skills))


def activate_environment(
    context: InstallContext,
    name: str,
    *,
    scope: EnvironmentScope = "project",
) -> None:
    """Materialize a canonical environment manifest into the current runtime."""
    _validate_or_raise(name)
    paths = environment_paths(context, scope=scope)
    with installation_lock(context), _scope_lock(paths, scope):
        state = load_environment_state(context)
        if state.active is not None:
            if state.active == name and state.scope == scope:
                raise EnvironmentError(
                    title="Environment Already Active",
                    lines=(f"The {scope} environment '{name}' is already active.",),
                    kind="info",
                )
            raise EnvironmentError(
                title="Environment Already Active",
                lines=("Deactivate the current environment before activating another one.",),
                exit_code=2,
            )

        current = load_runtime_snapshot(context)
        manifest = _load_manifest(paths, name, scope)
        revision = _manifest_revision(paths.manifest_path(name))
        previous_path = paths.previous_lockfile_path
        previous_content = previous_path.read_bytes() if previous_path.exists() else None

        with _materialized_environment(manifest) as materialized_skills:
            try:
                write_lockfile_path(
                    previous_path,
                    current.lockfile,
                    meta_updates={"version": LOCKFILE_VERSION, "mode": context.mode},
                )
                with RuntimeMutation(context) as mutation:
                    _replace_runtime(context, manifest, materialized_skills)
                    write_environment_state(
                        context,
                        EnvironmentState(
                            active=name,
                            scope=scope,
                            revision=revision,
                            previous_lockfile_present=current.lockfile_present,
                        ),
                    )
                    mutation.commit()
            except BaseException:
                _restore_file(previous_path, previous_content)
                raise


def deactivate_environment(context: InstallContext) -> bool:
    """Restore the runtime that preceded environment activation."""
    with installation_lock(context):
        state = load_environment_state(context)
        if state.active is None or state.scope is None:
            return False
        paths = environment_paths(context, scope=state.scope)
        with _scope_lock(paths, state.scope):
            load_runtime_snapshot(context)
            previous_path = paths.previous_lockfile_path
            if not previous_path.is_file() or previous_path.is_symlink():
                raise EnvironmentError(
                    title="Missing Previous Runtime",
                    lines=("The manifest needed to restore the previous runtime is missing.",),
                )
            previous = load_lockfile(previous_path, expected_mode=context.mode)
            previous_content = previous_path.read_bytes()
            with _materialized_environment(previous) as materialized_skills:
                with RuntimeMutation(context) as mutation:
                    _replace_runtime(context, previous, materialized_skills)
                    if not state.previous_lockfile_present:
                        context.lockfile_path.unlink(missing_ok=True)
                    previous_path.unlink()
                    try:
                        write_environment_state(context, EnvironmentState())
                    except BaseException:
                        _restore_file(previous_path, previous_content)
                        raise
                    mutation.commit()
            return True


def remove_environment(
    context: InstallContext,
    name: str,
    *,
    scope: EnvironmentScope = "project",
) -> None:
    """Remove one environment manifest from an explicit scope."""
    _validate_or_raise(name)
    paths = environment_paths(context, scope=scope)
    with installation_lock(context), _scope_lock(paths, scope):
        state = load_environment_state(context)
        if state.active == name and state.scope == scope:
            raise EnvironmentError(
                title="Environment Is Active",
                lines=("Deactivate the environment before removing it.",),
                exit_code=2,
            )
        manifest_path = paths.manifest_path(name)
        if not manifest_path.exists():
            raise EnvironmentError(
                title="Environment Not Found",
                lines=(f"No {scope} environment named '{name}' was found.",),
                exit_code=2,
            )
        _load_manifest(paths, name, scope)
        manifest_path.unlink()


def ensure_active_environment_runtime_is_clean(context: InstallContext) -> str | None:
    """Verify that the active manifest and runtime still match."""
    state = load_environment_state(context)
    if state.active is None or state.scope is None or state.revision is None:
        return None
    paths = environment_paths(context, scope=state.scope)
    with _scope_lock(paths, state.scope):
        manifest = _load_current_manifest(paths, state)
        runtime = load_runtime_snapshot(context)
        if not _lockfile_entries_match(runtime.lockfile, manifest):
            raise EnvironmentError(
                title="Active Environment Is Out Of Sync",
                lines=("Reactivate the environment before modifying its runtime.",),
                exit_code=2,
            )
    return state.active


def sync_active_environment(context: InstallContext) -> str | None:
    """Persist the current runtime to its active canonical manifest."""
    state = load_environment_state(context)
    if state.active is None or state.scope is None or state.revision is None:
        return None
    paths = environment_paths(context, scope=state.scope)
    with _scope_lock(paths, state.scope):
        manifest_path = paths.manifest_path(state.active)
        _load_current_manifest(paths, state)
        runtime = load_runtime_snapshot(context)
        manifest_content = manifest_path.read_bytes()
        state_content = paths.state_path.read_bytes()
        try:
            _write_manifest(manifest_path, runtime.lockfile, name=state.active, scope=state.scope)
            revision = _manifest_revision(manifest_path)
            write_environment_state(
                context,
                replace(state, revision=revision),
            )
        except BaseException:
            _restore_file(manifest_path, manifest_content)
            _restore_file(paths.state_path, state_content)
            raise
    return state.active


def load_runtime_snapshot(context: InstallContext) -> RuntimeSnapshot:
    """Load and verify a managed runtime before an environment operation."""
    lockfile_present = context.lockfile_path.exists()
    lockfile = load_lockfile(context.lockfile_path, expected_mode=context.mode)
    _validate_skill_tree(
        lockfile,
        context.skills_dir,
        lockfile_name=context.lockfile_path.name,
    )
    return RuntimeSnapshot(lockfile=lockfile, lockfile_present=lockfile_present)


def _validate_skill_tree(
    lockfile: LockfileData,
    skills_dir: Path,
    *,
    lockfile_name: str,
) -> None:
    managed_names = set(lockfile.skills)
    if skills_dir.is_symlink():
        raise EnvironmentError(
            title="Unsafe Skill Tree",
            lines=(f"The skill tree '{skills_dir}' is a symbolic link.",),
            exit_code=2,
        )
    if skills_dir.exists():
        unmanaged = sorted(child.name for child in skills_dir.iterdir() if child.name not in managed_names)
        if unmanaged:
            raise EnvironmentError(
                title="Unmanaged Skills Detected",
                lines=(f"The skill tree contains entries not tracked in {lockfile_name}: {', '.join(unmanaged)}.",),
                exit_code=2,
            )
    missing = sorted(
        name for name in managed_names if (skills_dir / name).is_symlink() or not (skills_dir / name).is_dir()
    )
    if missing:
        raise EnvironmentError(
            title="Missing Installed Skills",
            lines=(f"The lockfile references missing skills: {', '.join(missing)}.",),
            exit_code=2,
        )
    missing_hashes = sorted(name for name, entry in lockfile.skills.items() if entry.content_hash is None)
    if missing_hashes:
        raise EnvironmentError(
            title="Lockfile Missing Content Hashes",
            lines=(f"The lockfile does not have content hashes for: {', '.join(missing_hashes)}.",),
            exit_code=2,
        )
    dirty = [
        name
        for name, entry in sorted(lockfile.skills.items())
        if entry.content_hash is not None and hash_skill_directory(skills_dir / name) != entry.content_hash
    ]
    if dirty:
        raise EnvironmentError(
            title="Runtime Has Local Modifications",
            lines=(f"The skill tree differs from its lockfile hashes: {', '.join(dirty)}.",),
            exit_code=2,
        )


def _load_manifest(paths: EnvironmentPaths, name: str, scope: EnvironmentScope) -> LockfileData:
    _validate_manifest_storage(paths)
    manifest_path = paths.manifest_path(name)
    if not manifest_path.exists():
        raise EnvironmentError(
            title="Environment Not Found",
            lines=(f"No {scope} environment named '{name}' was found.",),
            exit_code=2,
        )
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise EnvironmentError(
            title="Invalid Environment Manifest",
            lines=(f"The manifest at '{manifest_path}' must be a regular file.",),
            exit_code=2,
        )
    manifest = load_lockfile(manifest_path, expected_mode=scope)
    if manifest.meta.get("environment") != name:
        raise EnvironmentError(
            title="Invalid Environment Manifest",
            lines=(f"The manifest at '{manifest_path}' belongs to another environment.",),
            exit_code=2,
        )
    missing_hashes = sorted(name for name, entry in manifest.skills.items() if entry.content_hash is None)
    if missing_hashes:
        raise EnvironmentError(
            title="Invalid Environment Manifest",
            lines=(f"The manifest is missing content hashes for: {', '.join(missing_hashes)}.",),
            exit_code=2,
        )
    return manifest


def _load_current_manifest(paths: EnvironmentPaths, state: EnvironmentState) -> LockfileData:
    if state.active is None or state.scope is None or state.revision is None:
        raise AssertionError("Active state is incomplete.")
    manifest = _load_manifest(paths, state.active, state.scope)
    if _manifest_revision(paths.manifest_path(state.active)) != state.revision:
        raise EnvironmentError(
            title="Environment Changed Elsewhere",
            lines=(
                f"The {state.scope} environment '{state.active}' changed after it was activated.",
                "Deactivate and reactivate it before applying more changes.",
            ),
            exit_code=2,
        )
    return manifest


def _write_manifest(
    manifest_path: Path,
    lockfile: LockfileData,
    *,
    name: str,
    scope: EnvironmentScope,
) -> None:
    manifest = LockfileData(
        meta={key: value for key, value in lockfile.meta.items() if key != "environment"},
        skills=dict(lockfile.skills),
    )
    write_lockfile_path(
        manifest_path,
        manifest,
        meta_updates={
            "version": LOCKFILE_VERSION,
            "mode": scope,
            "environment": name,
            "updated_at": utc_now(),
        },
    )


@contextmanager
def _materialized_environment(manifest: LockfileData) -> Iterator[Path]:
    with TemporaryDirectory(prefix="trivium-environment-") as temp_dir_name:
        skills_dir = Path(temp_dir_name) / "skills"
        skills_dir.mkdir()
        grouped: dict[tuple[str, str], list[tuple[str, SkillLockEntry]]] = {}
        for skill_name, entry in manifest.skills.items():
            grouped.setdefault((entry.source_url, entry.commit_hash), []).append((skill_name, entry))
        try:
            for (source_url, commit_hash), entries in sorted(grouped.items()):
                with cloned_repo_at_revision(source_url, commit_hash) as repo_path:
                    for skill_name, entry in entries:
                        container = (
                            repo_path if entry.skills_path == "." else resolve_repo_path(repo_path, entry.skills_path)
                        )
                        skill_dir = None if container is None else container / skill_name
                        if skill_dir is None or not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
                            raise EnvironmentError(
                                title="Environment Materialization Failed",
                                lines=(f"The pinned skill '{skill_name}' could not be found.",),
                            )
                        parsed_skill, issues = validate_skill_directory(skill_dir)
                        if parsed_skill is None or issues:
                            raise EnvironmentError(
                                title="Environment Materialization Failed",
                                lines=(f"The pinned skill '{skill_name}' is invalid.",),
                            )
                        if (
                            entry.content_hash is None
                            or hash_skill_directory(
                                parsed_skill.directory,
                                skill_document=render_skill_document(parsed_skill.frontmatter, parsed_skill.body),
                            )
                            != entry.content_hash
                        ):
                            raise EnvironmentError(
                                title="Environment Materialization Failed",
                                lines=(f"The pinned skill '{skill_name}' does not match its content hash.",),
                            )
                        install_skill_tree(parsed_skill, skills_dir / skill_name)
        except GitCloneError as error:
            lines = [error.stderr]
            if error.guidance is not None:
                lines.append(error.guidance)
            raise EnvironmentError(
                title="Environment Materialization Failed",
                lines=tuple(lines),
                exit_code=5 if error.auth_failure else 1,
            ) from error
        except GitCheckoutError as error:
            raise EnvironmentError(
                title="Environment Materialization Failed",
                lines=(error.stderr, f"The pinned commit '{error.revision}' is unavailable."),
                exit_code=5,
            ) from error
        yield skills_dir


def _replace_runtime(
    context: InstallContext,
    manifest: LockfileData,
    materialized_skills: Path,
) -> None:
    if context.skills_dir.is_symlink() or context.skills_dir.is_file():
        context.skills_dir.unlink()
    elif context.skills_dir.is_dir():
        shutil.rmtree(context.skills_dir)
    context.skills_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(materialized_skills, context.skills_dir)
    runtime = LockfileData(
        meta=dict(manifest.meta),
        skills={
            name: replace(entry, install_path=context.relative_install_path(name))
            for name, entry in manifest.skills.items()
        },
    )
    meta_updates: dict[str, object] = {
        "version": LOCKFILE_VERSION,
        "mode": context.mode,
        "updated_at": utc_now(),
    }
    runtime.meta.pop("environment", None)
    write_lockfile_path(context.lockfile_path, runtime, meta_updates=meta_updates)


def _lockfile_entries_match(first: LockfileData, second: LockfileData) -> bool:
    return {name: entry.to_toml_dict() for name, entry in first.skills.items()} == {
        name: entry.to_toml_dict() for name, entry in second.skills.items()
    }


def _manifest_revision(manifest_path: Path) -> str:
    return sha256(manifest_path.read_bytes()).hexdigest()


def _validate_manifest_storage(paths: EnvironmentPaths) -> None:
    for path in (paths.manifests_dir.parent, paths.manifests_dir):
        if path.is_symlink():
            raise EnvironmentError(
                title="Unsafe Environment Storage",
                lines=(f"The environment storage path '{path}' is a symbolic link.",),
                exit_code=2,
            )
        if path.exists() and not path.is_dir():
            raise EnvironmentError(
                title="Unsafe Environment Storage",
                lines=(f"The environment storage path '{path}' is not a directory.",),
                exit_code=2,
            )


@contextmanager
def _scope_lock(paths: EnvironmentPaths, scope: EnvironmentScope) -> Iterator[None]:
    lock = exclusive_file_lock(paths.lock_path) if scope == "global" else nullcontext()
    with lock:
        yield


def _write_bytes(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.", delete=False) as file:
            temporary_path = Path(file.name)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _restore_file(destination: Path, content: bytes | None) -> None:
    if content is None:
        destination.unlink(missing_ok=True)
    else:
        _write_bytes(destination, content)


def _validate_or_raise(name: str) -> None:
    message = validate_environment_name(name)
    if message is not None:
        raise EnvironmentError(title="Invalid Environment Name", lines=(message,), exit_code=2)
