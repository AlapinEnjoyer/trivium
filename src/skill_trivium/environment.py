"""Manage named snapshots of a verified installed-skill runtime.

Environments preserve lockfiles and skill trees, support project-local or
shared definitions, and can temporarily replace the default runtime. The
module also checks for unmanaged or modified skills before capturing or
switching snapshots.
"""

import shutil
import tomllib
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import tomli_w

from skill_trivium.context import resolve_install_context
from skill_trivium.git import GitCheckoutError, GitCloneError, cloned_repo_at_revision
from skill_trivium.lockfile import (
    LOCKFILE_VERSION,
    installation_lock,
    load_lockfile,
    write_lockfile,
    write_lockfile_path,
)
from skill_trivium.models import InstallContext, LockfileData
from skill_trivium.skills import (
    MAX_NAME_LENGTH,
    NAME_PATTERN,
    hash_skill_directory,
    install_skill_tree,
    utc_now,
    validate_skill_directory,
)

TRIVIUM_HOME_DIR = ".trivium"
PROJECTS_DIR = "projects"
GLOBAL_DIR = "global"
ENVS_DIR = "envs"
DEFAULT_DIR = "default"
STATE_FILE = "state.toml"
LOCKFILE_NAME = "skills.lock"
SHARED_ENVS_DIR = "environments"
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
    """Store the filesystem paths belonging to an environment scope."""

    state_path: Path
    default_dir: Path
    envs_dir: Path
    shared_envs_dir: Path | None

    def env_dir(self, name: str) -> Path:
        """Return the local directory for a named environment."""
        return self.envs_dir / name


@dataclass(frozen=True, slots=True)
class EnvironmentState:
    """Store the currently active environment name."""

    active: str | None = None


@dataclass(frozen=True, slots=True)
class EnvironmentRecord:
    """Summarize an environment for list output."""

    name: str
    active: bool
    local: bool
    shared: bool
    skill_count: int


@dataclass(frozen=True, slots=True)
class EnvironmentDetails:
    """Describe an environment and the skills in its snapshot."""

    name: str
    active: bool
    local: bool
    shared: bool
    skill_names: tuple[str, ...]


def validate_environment_name(name: str) -> str | None:
    """Return a validation error for an environment name, if it is invalid."""
    if not 1 <= len(name) <= MAX_NAME_LENGTH:
        return f"Environment names must be between 1 and {MAX_NAME_LENGTH} characters."
    if name.strip() != name:
        return "Environment names may not have surrounding whitespace."
    if any(character.isupper() for character in name):
        return "Environment names may only use lowercase letters, digits, and single hyphens."

    if NAME_PATTERN.fullmatch(name) is None:
        return "Environment names may only use lowercase letters, digits, and single hyphens."
    return None


def active_environment_name(context: InstallContext) -> str | None:
    """Return the active environment name for an installation context."""
    return load_environment_state(context).active


def load_environment_state(context: InstallContext) -> EnvironmentState:
    """Load the active environment state for an installation context.

    Args:
        context: Installation context whose project or global state file is
            read.

    Returns:
        The active environment name, or an empty state when no state file
        exists or no valid active name is stored.
    """
    state_path = environment_paths(context, scope=context.mode).state_path
    if not state_path.exists():
        return EnvironmentState()

    with state_path.open("rb") as file:
        payload = tomllib.load(file)

    active = payload.get("active")
    return EnvironmentState(active=active if isinstance(active, str) and active else None)


def write_environment_state(context: InstallContext, state: EnvironmentState) -> None:
    """Persist or clear the active environment state."""
    state_path = environment_paths(context, scope=context.mode).state_path
    if state.active is None:
        if state_path.exists():
            state_path.unlink()
        return

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(tomli_w.dumps({"active": state.active}), encoding="utf-8")


def list_environments(context: InstallContext, *, scope: EnvironmentScope | None = None) -> list[EnvironmentRecord]:
    """List local and shared environments visible in the requested scope.

    Args:
        context: Current installation context.
        scope: Scope to inspect. Defaults to the context's mode.

    Returns:
        Environment summaries sorted by environment name.
    """
    env_scope = scope or context.mode
    paths = environment_paths(context, scope=env_scope)
    state = load_environment_state(context)
    active_name = state.active if env_scope == context.mode else None
    names = set(_local_environment_names(paths))
    names.update(_shared_environment_names(paths))

    records: list[EnvironmentRecord] = []
    for name in sorted(names):
        lockfile = _load_preferred_environment_lockfile(paths, name)
        records.append(
            EnvironmentRecord(
                name=name,
                active=name == active_name,
                local=paths.env_dir(name).is_dir(),
                shared=_shared_environment_path(paths, name) is not None,
                skill_count=len(lockfile.skills),
            )
        )
    return records


def describe_environment(
    context: InstallContext,
    name: str | None = None,
    *,
    scope: EnvironmentScope | None = None,
) -> EnvironmentDetails | None:
    """Return details for a named environment or the active one."""
    state = load_environment_state(context)
    target_name = name or state.active
    if target_name is None:
        return None

    paths = environment_paths(context, scope=scope or context.mode)
    local = paths.env_dir(target_name).is_dir()
    shared = _shared_environment_path(paths, target_name) is not None
    if not local and not shared:
        return None

    lockfile = _load_preferred_environment_lockfile(paths, target_name)
    return EnvironmentDetails(
        name=target_name,
        active=(scope or context.mode) == context.mode and target_name == state.active,
        local=local,
        shared=shared,
        skill_names=tuple(sorted(lockfile.skills)),
    )


def create_environment(
    context: InstallContext,
    *,
    name: str,
    empty: bool,
    shared: bool,
    scope: EnvironmentScope | None = None,
) -> EnvironmentRecord:
    """Create an empty environment or snapshot the verified current runtime.

    Args:
        context: Installation context whose runtime may be captured.
        name: New environment name.
        empty: Whether to create an empty snapshot instead of capturing skills.
        shared: Whether to write a project-shared environment definition.
        scope: Storage scope. Defaults to the context's mode.

    Returns:
        A summary of the newly created environment.

    Raises:
        EnvironmentError: If the name is invalid, storage conflicts, or the
            runtime cannot be safely captured.
    """
    _validate_or_raise(name)
    env_scope = scope or context.mode
    paths = environment_paths(context, scope=env_scope)
    if shared and paths.shared_envs_dir is None:
        raise EnvironmentError(
            title="Shared Environments Unsupported",
            lines=("Shared environments are only available in project mode.",),
            exit_code=2,
        )
    local_dir = paths.env_dir(name)
    shared_path = _shared_environment_file(paths, name)
    local_exists = local_dir.is_dir()
    shared_exists = shared_path is not None and shared_path.exists()

    if empty:
        if local_exists:
            _raise_environment_exists(name, location="local")
        if shared and shared_exists:
            _raise_environment_exists(name, location="shared")
        lockfile = LockfileData()
        _write_environment_snapshot(
            local_dir,
            lockfile=lockfile,
            source_skills_dir=None,
            environment_name=name,
            mode=context.mode,
        )
        if shared:
            _write_shared_definition(context, name=name, lockfile=lockfile, scope=env_scope)
        return _build_environment_record(paths, name, active=False)

    if local_exists:
        if shared and not shared_exists:
            lockfile = load_lockfile(local_dir / LOCKFILE_NAME)
            _write_shared_definition(context, name=name, lockfile=lockfile, scope=env_scope)
            return _build_environment_record(
                paths,
                name,
                active=env_scope == context.mode and load_environment_state(context).active == name,
            )
        _raise_environment_exists(name, location="local-and-shared" if shared_exists else "local")
    if shared_exists:
        _raise_environment_exists(name, location="shared")

    capture_context = context if context.mode == "project" else _project_capture_context(context)
    snapshot = load_runtime_snapshot(capture_context, require_content_hashes=True)
    _write_environment_snapshot(
        local_dir,
        lockfile=snapshot.lockfile,
        source_skills_dir=snapshot.skills_dir,
        environment_name=name,
        mode=capture_context.mode,
    )
    if shared:
        _write_shared_definition(context, name=name, lockfile=snapshot.lockfile, scope=env_scope)
    return _build_environment_record(
        paths,
        name,
        active=env_scope == context.mode and load_environment_state(context).active == name,
    )


def activate_environment(context: InstallContext, name: str) -> None:
    """Activate an environment after preserving the current runtime snapshot.

    Args:
        context: Installation context whose runtime should be replaced.
        name: Environment to restore.

    Raises:
        EnvironmentError: If the environment is unavailable, already active,
            or the current runtime cannot be captured safely.
    """
    _validate_or_raise(name)
    with installation_lock(context):
        paths = environment_paths(context, scope=context.mode)
        state = load_environment_state(context)
        if state.active == name:
            raise EnvironmentError(
                title="Environment Already Active",
                lines=(f"The environment '{name}' is already active.",),
                kind="info",
            )

        _ensure_environment_available(context, name)
        if state.active is None:
            default_snapshot = load_runtime_snapshot(context, require_content_hashes=True)
            _write_default_snapshot(paths.default_dir, default_snapshot, mode=context.mode)
        else:
            sync_active_environment(context)

        _restore_snapshot(paths.env_dir(name), context)
        write_environment_state(context, EnvironmentState(active=name))


def deactivate_environment(context: InstallContext) -> bool:
    """Restore the default runtime snapshot and clear active state.

    Args:
        context: Installation context whose active environment is restored.

    Returns:
        ``True`` when an environment was active and deactivated; otherwise
        ``False``.

    Raises:
        EnvironmentError: If the active runtime or default snapshot is missing
            or inconsistent.
    """
    return _deactivate_environment(context, sync_active=True)


def deactivate_environment_without_sync(context: InstallContext) -> bool:
    """Deactivate without first copying the current runtime into the environment."""
    return _deactivate_environment(context, sync_active=False)


def _deactivate_environment(context: InstallContext, *, sync_active: bool) -> bool:
    paths = environment_paths(context, scope=context.mode)
    state = load_environment_state(context)
    if state.active is None:
        return False

    if sync_active:
        sync_active_environment(context)
    if not paths.default_dir.is_dir():
        raise EnvironmentError(
            title="Missing Default Snapshot",
            lines=(
                "The original runtime snapshot is missing.",
                "Remove the active environment state or recreate the environment locally before deactivating.",
            ),
        )

    _restore_snapshot(paths.default_dir, context)
    write_environment_state(context, EnvironmentState())
    return True


def remove_environment(
    context: InstallContext,
    name: str,
    *,
    scope: EnvironmentScope | None = None,
) -> tuple[bool, bool, bool]:
    """Remove an environment and report active, local, and shared state."""
    _validate_or_raise(name)
    env_scope = scope or context.mode
    paths = environment_paths(context, scope=env_scope)
    local_path = paths.env_dir(name)
    shared_path = _shared_environment_path(paths, name)
    local_exists = local_path.is_dir()
    shared_exists = shared_path is not None
    if not local_exists and not shared_exists:
        raise EnvironmentError(
            title="Environment Not Found",
            lines=(f"No local or shared environment named '{name}' was found.",),
            exit_code=2,
        )

    was_active = env_scope == context.mode and active_environment_name(context) == name
    if was_active:
        deactivate_environment_without_sync(context)

    if local_exists:
        shutil.rmtree(local_path)
    if shared_path is not None and shared_path.exists():
        shared_path.unlink()
    return was_active, local_exists, shared_exists


def ensure_active_environment_runtime_is_clean(context: InstallContext) -> str | None:
    """Verify the active runtime and return its environment name."""
    active = active_environment_name(context)
    if active is None:
        return None
    load_runtime_snapshot(context, require_content_hashes=True)
    return active


def sync_active_environment(context: InstallContext) -> str | None:
    """Update the active environment snapshot from the current runtime."""
    active = active_environment_name(context)
    if active is None:
        return None

    paths = environment_paths(context, scope=context.mode)
    local_dir = paths.env_dir(active)
    if not local_dir.is_dir():
        raise EnvironmentError(
            title="Missing Environment Snapshot",
            lines=(
                f"The active environment '{active}' is missing from local storage.",
                "Deactivate the environment or recreate it before continuing.",
            ),
        )

    snapshot = load_runtime_snapshot(context, require_content_hashes=True)
    _write_environment_snapshot(
        local_dir,
        lockfile=snapshot.lockfile,
        source_skills_dir=snapshot.skills_dir,
        environment_name=active,
        mode=context.mode,
    )
    _rewrite_runtime_lockfile(context, snapshot.lockfile, environment_name=active)
    if _shared_environment_path(paths, active) is not None:
        _write_shared_definition(context, name=active, lockfile=snapshot.lockfile, scope=context.mode)
    return active


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    """Represent a verified runtime lockfile and skills directory."""

    lockfile: LockfileData
    skills_dir: Path
    lockfile_present: bool


def load_runtime_snapshot(context: InstallContext, *, require_content_hashes: bool) -> RuntimeSnapshot:
    """Load and verify runtime state before an environment operation.

    Args:
        context: Installation context whose lockfile and skill tree are checked.
        require_content_hashes: Whether every managed skill must have a content
            hash before the snapshot is accepted.

    Returns:
        A verified lockfile, skills directory, and lockfile-presence flag.

    Raises:
        EnvironmentError: If unmanaged, missing, modified, or unverifiable
            skills are found.
    """
    lockfile_present = context.lockfile_path.exists()
    lockfile = load_lockfile(context.lockfile_path, expected_mode=context.mode)
    skills_dir = context.skills_dir
    managed_names = set(lockfile.skills)
    if skills_dir.exists():
        unmanaged = sorted(
            child.name for child in skills_dir.iterdir() if child.is_dir() and child.name not in managed_names
        )
        if unmanaged:
            formatted = ", ".join(unmanaged)
            raise EnvironmentError(
                title="Unmanaged Skills Detected",
                lines=(
                    f"The runtime contains skill directories that are not tracked in {context.lockfile_path.name}: {formatted}.",
                    "Only lockfile-managed skills can be captured into an environment.",
                ),
                exit_code=2,
            )

    missing = sorted(name for name in managed_names if not context.install_path_for(name).is_dir())
    if missing:
        formatted = ", ".join(missing)
        raise EnvironmentError(
            title="Missing Installed Skills",
            lines=(
                f"The lockfile references skills that are missing on disk: {formatted}.",
                "Run `trv update` or remove the broken skills before using environments.",
            ),
            exit_code=2,
        )

    if require_content_hashes:
        missing_hashes = sorted(name for name, entry in lockfile.skills.items() if entry.content_hash is None)
        if missing_hashes:
            formatted = ", ".join(missing_hashes)
            raise EnvironmentError(
                title="Lockfile Missing Content Hashes",
                lines=(
                    f"The runtime lockfile does not have content hashes for: {formatted}.",
                    "Run `trv update` first so trivium can verify the runtime before capturing an environment.",
                ),
                exit_code=2,
            )

    dirty = []
    for name, entry in sorted(lockfile.skills.items()):
        if entry.content_hash is None:
            continue
        if hash_skill_directory(context.install_path_for(name)) != entry.content_hash:
            dirty.append(name)
    if dirty:
        formatted = ", ".join(dirty)
        raise EnvironmentError(
            title="Runtime Has Local Modifications",
            lines=(
                f"The installed skills differ from the recorded lockfile hashes: {formatted}.",
                "Update or reinstall those skills before capturing or switching environments.",
            ),
            exit_code=2,
        )

    return RuntimeSnapshot(lockfile=lockfile, skills_dir=skills_dir, lockfile_present=lockfile_present)


def environment_paths(context: InstallContext, *, scope: EnvironmentScope) -> EnvironmentPaths:
    """Resolve storage paths for a project or global environment scope."""
    if scope == "global":
        root = Path.home() / TRIVIUM_HOME_DIR / GLOBAL_DIR
        shared_envs_dir: Path | None = None
    else:
        root = Path.home() / TRIVIUM_HOME_DIR / PROJECTS_DIR / _project_identifier(context.base_dir)
        shared_envs_dir = context.base_dir / ".agents" / SHARED_ENVS_DIR
    return EnvironmentPaths(
        state_path=root / STATE_FILE,
        default_dir=root / DEFAULT_DIR,
        envs_dir=root / ENVS_DIR,
        shared_envs_dir=shared_envs_dir,
    )


def _build_environment_record(paths: EnvironmentPaths, name: str, *, active: bool) -> EnvironmentRecord:
    lockfile = _load_preferred_environment_lockfile(paths, name)
    return EnvironmentRecord(
        name=name,
        active=active,
        local=paths.env_dir(name).is_dir(),
        shared=_shared_environment_path(paths, name) is not None,
        skill_count=len(lockfile.skills),
    )


def _copy_directory(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    if not source.exists():
        return
    shutil.copytree(source, destination)


def _ensure_environment_available(context: InstallContext, name: str) -> None:
    runtime_paths = environment_paths(context, scope=context.mode)
    if runtime_paths.env_dir(name).is_dir():
        return

    if context.mode == "project":
        global_paths = environment_paths(context, scope="global")
        if global_paths.env_dir(name).is_dir():
            _copy_directory(global_paths.env_dir(name), runtime_paths.env_dir(name))
            return
    else:
        global_paths = runtime_paths

    shared_path = _shared_environment_path(runtime_paths, name)
    if shared_path is None and context.mode == "project":
        shared_path = _shared_environment_path(global_paths, name)
    if shared_path is None:
        raise EnvironmentError(
            title="Environment Not Found",
            lines=(f"No local or shared environment named '{name}' was found.",),
            exit_code=2,
        )

    try:
        _materialize_shared_environment(context, name, shared_path)
    except GitCloneError as error:
        lines = [error.stderr]
        if error.guidance is not None:
            lines.append(error.guidance)
        raise EnvironmentError(title="Environment Materialization Failed", lines=tuple(lines), exit_code=5) from error
    except GitCheckoutError as error:
        raise EnvironmentError(
            title="Environment Materialization Failed",
            lines=(
                error.stderr,
                f"The pinned commit '{error.revision}' could not be checked out from '{error.source_url}'.",
            ),
        ) from error


def _load_preferred_environment_lockfile(paths: EnvironmentPaths, name: str) -> LockfileData:
    local_path = paths.env_dir(name) / LOCKFILE_NAME
    if local_path.is_file():
        return load_lockfile(local_path)
    shared_path = _shared_environment_path(paths, name)
    if shared_path is not None:
        return load_lockfile(shared_path)
    return LockfileData()


def _local_environment_names(paths: EnvironmentPaths) -> list[str]:
    if not paths.envs_dir.is_dir():
        return []
    return sorted(child.name for child in paths.envs_dir.iterdir() if child.is_dir())


def _materialize_shared_environment(context: InstallContext, name: str, shared_path: Path) -> None:
    manifest = load_lockfile(shared_path)
    target_dir = environment_paths(context, scope=context.mode).env_dir(name)

    with TemporaryDirectory(prefix="trivium-env-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        temp_env_dir = temp_dir / name
        temp_skills_dir = temp_env_dir / "skills"
        temp_skills_dir.mkdir(parents=True, exist_ok=True)

        grouped_entries: dict[tuple[str, str], list[tuple[str, object]]] = {}
        for skill_name, entry in manifest.skills.items():
            grouped_entries.setdefault((entry.source_url, entry.commit_hash), []).append((skill_name, entry))

        for (source_url, commit_hash), entries in sorted(grouped_entries.items()):
            with cloned_repo_at_revision(source_url, commit_hash) as repo_path:
                for skill_name, entry in entries:
                    container = repo_path if entry.skills_path == "." else repo_path / entry.skills_path  # ty:ignore[unresolved-attribute]
                    skill_dir = container / skill_name
                    if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
                        raise EnvironmentError(
                            title="Environment Materialization Failed",
                            lines=(
                                f"The shared environment references '{skill_name}' at '{entry.skills_path}/{skill_name}', but it was not found.",  # ty:ignore[unresolved-attribute]
                                f"Source: {source_url} @ {commit_hash}",
                            ),
                        )
                    parsed_skill, issues = validate_skill_directory(skill_dir)
                    if issues or parsed_skill is None:
                        issue_lines = [f"{issue.field}: {issue.rule}" for issue in issues]
                        raise EnvironmentError(
                            title="Environment Materialization Failed",
                            lines=(
                                f"The shared environment references '{skill_name}', but the pinned skill is invalid.",
                                *issue_lines,
                            ),
                        )
                    install_skill_tree(parsed_skill, temp_skills_dir / skill_name)

        _write_environment_snapshot(
            target_dir,
            lockfile=manifest,
            source_skills_dir=temp_skills_dir,
            environment_name=name,
            mode=context.mode,
        )


def _project_identifier(project_root: Path) -> str:
    return sha256(project_root.resolve().as_posix().encode("utf-8")).hexdigest()


def _restore_snapshot(snapshot_dir: Path, context: InstallContext) -> None:
    if not snapshot_dir.is_dir():
        raise EnvironmentError(
            title="Missing Environment Snapshot",
            lines=(f"The snapshot at '{snapshot_dir}' does not exist.",),
        )

    snapshot_lockfile = snapshot_dir / LOCKFILE_NAME
    lockfile = load_lockfile(snapshot_lockfile) if snapshot_lockfile.is_file() else None
    snapshot_skills_dir = snapshot_dir / "skills"
    context.skills_dir.parent.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix=".trivium-restore-", dir=context.skills_dir.parent) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        staged_skills = temp_dir / "skills"
        previous_skills = temp_dir / "previous-skills"
        if snapshot_skills_dir.exists():
            shutil.copytree(snapshot_skills_dir, staged_skills)

        if context.skills_dir.exists():
            context.skills_dir.replace(previous_skills)
        try:
            if staged_skills.exists():
                staged_skills.replace(context.skills_dir)
            if lockfile is not None:
                write_lockfile(context, lockfile)
            elif context.lockfile_path.exists():
                context.lockfile_path.unlink()
        except OSError:
            if context.skills_dir.exists():
                shutil.rmtree(context.skills_dir)
            if previous_skills.exists():
                previous_skills.replace(context.skills_dir)
            raise


def _rewrite_runtime_lockfile(context: InstallContext, lockfile: LockfileData, *, environment_name: str) -> None:
    write_lockfile_path(
        context.lockfile_path,
        lockfile,
        meta_updates={
            "version": LOCKFILE_VERSION,
            "mode": context.mode,
            "environment": environment_name,
            "updated_at": utc_now(),
        },
    )


def _shared_environment_file(paths: EnvironmentPaths, name: str) -> Path | None:
    if paths.shared_envs_dir is None:
        return None
    return paths.shared_envs_dir / f"{name}.lock"


def _shared_environment_names(paths: EnvironmentPaths) -> list[str]:
    if paths.shared_envs_dir is None or not paths.shared_envs_dir.is_dir():
        return []
    return sorted(path.stem for path in paths.shared_envs_dir.glob("*.lock") if path.is_file())


def _shared_environment_path(paths: EnvironmentPaths, name: str) -> Path | None:
    shared_file = _shared_environment_file(paths, name)
    if shared_file is None or not shared_file.is_file():
        return None
    return shared_file


def _validate_or_raise(name: str) -> None:
    message = validate_environment_name(name)
    if message is None:
        return
    raise EnvironmentError(title="Invalid Environment Name", lines=(message,), exit_code=2)


def _raise_environment_exists(name: str, *, location: Literal["local", "local-and-shared", "shared"]) -> None:
    if location == "local-and-shared":
        message = f"The environment '{name}' already exists locally and as a shared definition."
    elif location == "shared":
        message = f"The shared environment '{name}' already exists."
    else:
        message = f"The environment '{name}' already exists locally."
    raise EnvironmentError(title="Environment Exists", lines=(message,))


def _write_environment_snapshot(
    destination: Path,
    *,
    lockfile: LockfileData,
    source_skills_dir: Path | None,
    environment_name: str,
    mode: str,
) -> None:
    _replace_snapshot_dir(
        destination,
        lockfile=lockfile,
        source_skills_dir=source_skills_dir,
        create_skills_dir=True,
        environment_name=environment_name,
        mode=mode,
    )


def _write_default_snapshot(destination: Path, snapshot: RuntimeSnapshot, *, mode: str) -> None:
    _replace_snapshot_dir(
        destination,
        lockfile=snapshot.lockfile if snapshot.lockfile_present else None,
        source_skills_dir=snapshot.skills_dir if snapshot.skills_dir.exists() else None,
        create_skills_dir=snapshot.skills_dir.exists(),
        environment_name=None,
        mode=mode,
    )


def _write_shared_definition(
    context: InstallContext,
    *,
    name: str,
    lockfile: LockfileData,
    scope: EnvironmentScope,
) -> None:
    paths = environment_paths(context, scope=scope)
    shared_path = _shared_environment_file(paths, name)
    if shared_path is None:
        raise EnvironmentError(
            title="Shared Environments Unsupported",
            lines=("Shared environments are only available in project mode.",),
            exit_code=2,
        )

    write_lockfile_path(
        shared_path,
        lockfile,
        meta_updates={
            "version": LOCKFILE_VERSION,
            "mode": context.mode,
            "environment": name,
            "shared": True,
            "updated_at": utc_now(),
        },
    )


def _project_capture_context(context: InstallContext) -> InstallContext:
    if context.mode == "project":
        return context
    return resolve_install_context(False)


def _replace_snapshot_dir(
    destination: Path,
    *,
    lockfile: LockfileData | None,
    source_skills_dir: Path | None,
    create_skills_dir: bool,
    environment_name: str | None,
    mode: str,
) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    if create_skills_dir:
        (destination / "skills").mkdir(parents=True, exist_ok=True)
    if source_skills_dir is not None and source_skills_dir.exists():
        _copy_directory(source_skills_dir, destination / "skills")

    if lockfile is not None:
        meta_updates = {
            "version": LOCKFILE_VERSION,
            "mode": mode,
            "updated_at": utc_now(),
        }
        if environment_name is not None:
            meta_updates["environment"] = environment_name
        write_lockfile_path(destination / LOCKFILE_NAME, lockfile, meta_updates=meta_updates)
