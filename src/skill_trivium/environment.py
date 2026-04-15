import shutil
import tomllib
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

import tomli_w

from skill_trivium.git import GitCheckoutError, GitCloneError, cloned_repo_at_revision
from skill_trivium.lockfile import LOCKFILE_VERSION, load_lockfile, write_lockfile_path
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


@dataclass(frozen=True, slots=True)
class EnvironmentError(Exception):
    title: str
    lines: tuple[str, ...]
    exit_code: int = 1
    kind: str = "err"

    def __str__(self) -> str:
        return " ".join(self.lines)


@dataclass(frozen=True, slots=True)
class EnvironmentPaths:
    state_path: Path
    default_dir: Path
    envs_dir: Path
    shared_envs_dir: Path | None

    def env_dir(self, name: str) -> Path:
        return self.envs_dir / name


@dataclass(frozen=True, slots=True)
class EnvironmentState:
    active: str | None = None


@dataclass(frozen=True, slots=True)
class EnvironmentRecord:
    name: str
    active: bool
    local: bool
    shared: bool
    skill_count: int


@dataclass(frozen=True, slots=True)
class EnvironmentDetails:
    name: str
    active: bool
    local: bool
    shared: bool
    skill_names: tuple[str, ...]


def validate_environment_name(name: str) -> str | None:
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
    return load_environment_state(context).active


def load_environment_state(context: InstallContext) -> EnvironmentState:
    state_path = environment_paths(context).state_path
    if not state_path.exists():
        return EnvironmentState()

    with state_path.open("rb") as file:
        payload = tomllib.load(file)

    active = payload.get("active")
    return EnvironmentState(active=active if isinstance(active, str) and active else None)


def write_environment_state(context: InstallContext, state: EnvironmentState) -> None:
    state_path = environment_paths(context).state_path
    if state.active is None:
        if state_path.exists():
            state_path.unlink()
        return

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(tomli_w.dumps({"active": state.active}), encoding="utf-8")


def list_environments(context: InstallContext) -> list[EnvironmentRecord]:
    paths = environment_paths(context)
    state = load_environment_state(context)
    names = set(_local_environment_names(paths))
    names.update(_shared_environment_names(paths))

    records: list[EnvironmentRecord] = []
    for name in sorted(names):
        lockfile = _load_preferred_environment_lockfile(paths, name)
        records.append(
            EnvironmentRecord(
                name=name,
                active=name == state.active,
                local=paths.env_dir(name).is_dir(),
                shared=_shared_environment_path(paths, name) is not None,
                skill_count=len(lockfile.skills),
            )
        )
    return records


def describe_environment(context: InstallContext, name: str | None = None) -> EnvironmentDetails | None:
    state = load_environment_state(context)
    target_name = name or state.active
    if target_name is None:
        return None

    paths = environment_paths(context)
    local = paths.env_dir(target_name).is_dir()
    shared = _shared_environment_path(paths, target_name) is not None
    if not local and not shared:
        return None

    lockfile = _load_preferred_environment_lockfile(paths, target_name)
    return EnvironmentDetails(
        name=target_name,
        active=target_name == state.active,
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
) -> EnvironmentRecord:
    _validate_or_raise(name)
    paths = environment_paths(context)
    local_dir = paths.env_dir(name)
    shared_path = _shared_environment_file(paths, name)
    local_exists = local_dir.is_dir()
    shared_exists = shared_path is not None and shared_path.exists()

    if empty and local_exists:
        raise EnvironmentError(
            title="Environment Exists",
            lines=(f"The environment '{name}' already exists locally.",),
        )

    if empty and shared and shared_exists:
        raise EnvironmentError(
            title="Environment Exists",
            lines=(f"The shared environment '{name}' already exists.",),
        )

    if empty:
        lockfile = LockfileData()
        _write_environment_snapshot(
            local_dir,
            lockfile=lockfile,
            create_skills_dir=True,
            environment_name=name,
            mode=context.mode,
        )
        if shared:
            _write_shared_definition(context, name=name, lockfile=lockfile)
        return _build_environment_record(paths, name, active=False)

    if local_exists and not shared:
        raise EnvironmentError(
            title="Environment Exists",
            lines=(f"The environment '{name}' already exists locally.",),
        )

    if local_exists and shared_exists:
        raise EnvironmentError(
            title="Environment Exists",
            lines=(f"The environment '{name}' already exists locally and as a shared definition.",),
        )

    if shared_exists and not local_exists:
        raise EnvironmentError(
            title="Environment Exists",
            lines=(f"The shared environment '{name}' already exists.",),
        )

    if local_exists and shared and not shared_exists:
        lockfile = load_lockfile(local_dir / LOCKFILE_NAME)
        _write_shared_definition(context, name=name, lockfile=lockfile)
        return _build_environment_record(paths, name, active=load_environment_state(context).active == name)

    snapshot = load_runtime_snapshot(context, require_content_hashes=True)
    _write_environment_snapshot(
        local_dir,
        lockfile=snapshot.lockfile,
        create_skills_dir=True,
        environment_name=name,
        mode=context.mode,
    )
    _copy_directory(snapshot.skills_dir, local_dir / "skills")
    if shared:
        _write_shared_definition(context, name=name, lockfile=snapshot.lockfile)
    return _build_environment_record(paths, name, active=load_environment_state(context).active == name)


def activate_environment(context: InstallContext, name: str) -> None:
    _validate_or_raise(name)
    paths = environment_paths(context)
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
        _write_snapshot_dir(
            paths.default_dir,
            lockfile=default_snapshot.lockfile,
            source_skills_dir=default_snapshot.skills_dir,
            write_lockfile=default_snapshot.lockfile_present,
            create_skills_dir=default_snapshot.skills_dir.exists(),
            environment_name=None,
            mode=context.mode,
        )
    else:
        sync_active_environment(context)

    _restore_snapshot(paths.env_dir(name), context)
    write_environment_state(context, EnvironmentState(active=name))


def deactivate_environment(context: InstallContext) -> bool:
    paths = environment_paths(context)
    state = load_environment_state(context)
    if state.active is None:
        return False

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


def deactivate_environment_without_sync(context: InstallContext) -> bool:
    paths = environment_paths(context)
    state = load_environment_state(context)
    if state.active is None:
        return False

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


def remove_environment(context: InstallContext, name: str) -> tuple[bool, bool, bool]:
    _validate_or_raise(name)
    paths = environment_paths(context)
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

    was_active = active_environment_name(context) == name
    if was_active:
        deactivate_environment_without_sync(context)

    if local_exists:
        shutil.rmtree(local_path)
    if shared_path is not None and shared_path.exists():
        shared_path.unlink()
    return was_active, local_exists, shared_exists


def ensure_active_environment_runtime_is_clean(context: InstallContext) -> str | None:
    active = active_environment_name(context)
    if active is None:
        return None
    load_runtime_snapshot(context, require_content_hashes=True)
    return active


def ensure_environment_init_allowed(context: InstallContext) -> None:
    active = active_environment_name(context)
    if active is None:
        return
    raise EnvironmentError(
        title="Init Blocked While Environment Is Active",
        lines=(
            f"The environment '{active}' is active.",
            "`trv init` creates unmanaged local skills, so deactivate the environment first.",
        ),
        exit_code=2,
    )


def sync_active_environment(context: InstallContext) -> str | None:
    active = active_environment_name(context)
    if active is None:
        return None

    paths = environment_paths(context)
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
    _write_snapshot_dir(
        local_dir,
        lockfile=snapshot.lockfile,
        source_skills_dir=snapshot.skills_dir,
        write_lockfile=True,
        create_skills_dir=True,
        environment_name=active,
        mode=context.mode,
    )
    _rewrite_runtime_lockfile(context, snapshot.lockfile, environment_name=active)
    if _shared_environment_path(paths, active) is not None:
        _write_shared_definition(context, name=active, lockfile=snapshot.lockfile)
    return active


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    lockfile: LockfileData
    skills_dir: Path
    lockfile_present: bool


def load_runtime_snapshot(context: InstallContext, *, require_content_hashes: bool) -> RuntimeSnapshot:
    lockfile_present = context.lockfile_path.exists()
    lockfile = load_lockfile(context.lockfile_path)
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


def environment_paths(context: InstallContext) -> EnvironmentPaths:
    if context.mode == "global":
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
    paths = environment_paths(context)
    if paths.env_dir(name).is_dir():
        return

    shared_path = _shared_environment_path(paths, name)
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


def _load_environment_manifest(shared_path: Path) -> LockfileData:
    return load_lockfile(shared_path)


def _local_environment_names(paths: EnvironmentPaths) -> list[str]:
    if not paths.envs_dir.is_dir():
        return []
    return sorted(child.name for child in paths.envs_dir.iterdir() if child.is_dir())


def _materialize_shared_environment(context: InstallContext, name: str, shared_path: Path) -> None:
    manifest = _load_environment_manifest(shared_path)
    target_dir = environment_paths(context).env_dir(name)

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
                    container = repo_path if entry.skills_path == "." else repo_path / entry.skills_path
                    skill_dir = container / skill_name
                    if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
                        raise EnvironmentError(
                            title="Environment Materialization Failed",
                            lines=(
                                f"The shared environment references '{skill_name}' at '{entry.skills_path}/{skill_name}', but it was not found.",
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

        _write_snapshot_dir(
            target_dir,
            lockfile=manifest,
            source_skills_dir=temp_skills_dir,
            write_lockfile=True,
            create_skills_dir=True,
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

    snapshot_skills_dir = snapshot_dir / "skills"
    if context.skills_dir.exists():
        shutil.rmtree(context.skills_dir)
    if snapshot_skills_dir.exists():
        context.skills_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(snapshot_skills_dir, context.skills_dir, dirs_exist_ok=False)

    snapshot_lockfile = snapshot_dir / LOCKFILE_NAME
    if snapshot_lockfile.is_file():
        context.lockfile_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot_lockfile, context.lockfile_path)
    elif context.lockfile_path.exists():
        context.lockfile_path.unlink()


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


def _write_environment_snapshot(
    destination: Path,
    *,
    lockfile: LockfileData,
    create_skills_dir: bool,
    environment_name: str,
    mode: str,
) -> None:
    _write_snapshot_dir(
        destination,
        lockfile=lockfile,
        source_skills_dir=None,
        write_lockfile=True,
        create_skills_dir=create_skills_dir,
        environment_name=environment_name,
        mode=mode,
    )


def _write_shared_definition(context: InstallContext, *, name: str, lockfile: LockfileData) -> None:
    paths = environment_paths(context)
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


def _write_snapshot_dir(
    destination: Path,
    *,
    lockfile: LockfileData,
    source_skills_dir: Path | None,
    write_lockfile: bool,
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

    if write_lockfile:
        meta_updates = {
            "version": LOCKFILE_VERSION,
            "mode": mode,
            "updated_at": utc_now(),
        }
        if environment_name is not None:
            meta_updates["environment"] = environment_name
        write_lockfile_path(destination / LOCKFILE_NAME, lockfile, meta_updates=meta_updates)
