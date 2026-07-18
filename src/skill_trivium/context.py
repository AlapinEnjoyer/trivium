"""Resolve filesystem locations for project-scoped and global installations.

Project mode follows the nearest Git directory when one exists; global mode
uses the user's agent directory. The resulting context keeps paths and mode
consistent for every command that reads or writes installed skills.
"""

from pathlib import Path

from skill_trivium.models import InstallContext


def find_git_root(start: Path | None = None) -> Path | None:
    """Find the nearest Git root at or above a starting directory.

    Args:
        start: Directory from which to begin searching. Defaults to the current
            working directory.

    Returns:
        The first ancestor containing a ``.git`` entry, or ``None`` when the
        directory is not inside a Git working tree.
    """
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def resolve_install_context(global_mode: bool, cwd: Path | None = None) -> InstallContext:
    """Resolve all filesystem paths used by project or global mode.

    Args:
        global_mode: Whether to use the user's global agent directory instead
            of a project-local ``.agents`` directory.
        cwd: Directory to inspect for a Git root in project mode. Defaults to
            the current working directory.

    Returns:
        An installation context containing the mode, base directory, skill
        directory, lockfile path, and lockfile-relative install prefix.
    """
    current = (cwd or Path.cwd()).resolve()
    if global_mode:
        return _global_context()

    project_root = find_git_root(current) or current
    install_prefix = Path(".agents") / "skills"
    return InstallContext(
        mode="project",
        base_dir=project_root,
        skills_dir=project_root / install_prefix,
        lockfile_path=project_root / "skills.lock",
        install_prefix=install_prefix,
    )


def ensure_storage(context: InstallContext) -> None:
    """Create the directories required by an installation context.

    Args:
        context: Installation context whose lockfile parent and skills
            directory should exist.
    """
    context.lockfile_path.parent.mkdir(parents=True, exist_ok=True)
    context.skills_dir.mkdir(parents=True, exist_ok=True)


def _global_context() -> InstallContext:
    base_dir = Path.home() / ".agents"
    install_prefix = Path("skills")
    return InstallContext(
        mode="global",
        base_dir=base_dir,
        skills_dir=base_dir / install_prefix,
        lockfile_path=base_dir / "skills.lock",
        install_prefix=install_prefix,
    )
