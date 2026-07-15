"""Remove installed skills within one serialized mutation boundary."""

import shutil
from dataclasses import dataclass

from skill_trivium.environment import ensure_active_environment_runtime_is_clean, sync_active_environment
from skill_trivium.lockfile import installation_lock, load_lockfile, write_lockfile
from skill_trivium.models import InstallContext
from skill_trivium.mutation import RuntimeMutation


@dataclass(frozen=True, slots=True)
class RemoveOutcome:
    """Describe skills removed or found missing after locking the runtime."""

    removed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()


def run_remove(context: InstallContext, skill_names: list[str]) -> RemoveOutcome:
    """Remove installed skills and synchronize the active environment.

    The lockfile is reloaded after acquiring the installation lock so callers
    cannot mutate using state that changed while confirmation was displayed.

    Args:
        context: Installation context to mutate.
        skill_names: Validated installation names requested for removal.

    Returns:
        Removed names, or names no longer present after acquiring the lock.

    Raises:
        EnvironmentError: If an active environment is not safe to mutate or
            cannot be synchronized after removal.
    """
    target_names = tuple(dict.fromkeys(skill_names))
    with installation_lock(context):
        ensure_active_environment_runtime_is_clean(context)
        lockfile = load_lockfile(context.lockfile_path, expected_mode=context.mode)
        missing = tuple(name for name in target_names if name not in lockfile.skills)
        if missing:
            return RemoveOutcome(missing=missing)

        with RuntimeMutation(context) as mutation:
            for name in target_names:
                skill_dir = context.install_path_for(name)
                if skill_dir.exists():
                    shutil.rmtree(skill_dir)
                lockfile.skills.pop(name)

            write_lockfile(context, lockfile)
            mutation.commit()
        sync_active_environment(context)
    return RemoveOutcome(removed=target_names)
