"""Remove installed skills within one serialized mutation boundary."""

import shutil
from dataclasses import dataclass

from skill_trivium.environment import ensure_active_environment_runtime_is_clean, sync_active_environment
from skill_trivium.lockfile import load_lockfile, write_lockfile
from skill_trivium.models import InstallContext
from skill_trivium.mutation import RuntimeMutation


@dataclass(frozen=True, slots=True)
class RemoveOutcome:
    """Describe skills removed or found missing after locking the runtime."""

    removed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()


def run_remove(context: InstallContext, skill_names: list[str]) -> RemoveOutcome:
    """Remove installed skills and synchronize the active environment."""
    target_names = tuple(dict.fromkeys(skill_names))
    ensure_active_environment_runtime_is_clean(context)
    lockfile = load_lockfile(context.lockfile_path, expected_mode=context.mode)
    missing = tuple(name for name in target_names if name not in lockfile.skills)
    if missing:
        return RemoveOutcome(missing=missing)

    with RuntimeMutation(context) as mutation:
        for name in target_names:
            skill_dir = context.install_path_for(name)
            if skill_dir.is_symlink() or skill_dir.is_file():
                skill_dir.unlink()
            elif skill_dir.is_dir():
                shutil.rmtree(skill_dir)
            lockfile.skills.pop(name)

        if lockfile.skills:
            write_lockfile(context, lockfile)
        else:
            context.lockfile_path.unlink(missing_ok=True)
        sync_active_environment(context)
        mutation.commit()
    return RemoveOutcome(removed=target_names)
