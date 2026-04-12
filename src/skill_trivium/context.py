from pathlib import Path

from skill_trivium.models import InstallContext


def find_git_root(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def resolve_install_context(global_mode: bool, cwd: Path | None = None) -> InstallContext:
    current = (cwd or Path.cwd()).resolve()
    if global_mode:
        return _global_context()

    git_root = find_git_root(current)
    if git_root is None:
        return _global_context()

    install_prefix = Path(".agents") / "skills"
    return InstallContext(
        mode="project",
        base_dir=git_root,
        skills_dir=git_root / install_prefix,
        lockfile_path=git_root / "skills.lock",
        install_prefix=install_prefix,
    )


def ensure_storage(context: InstallContext) -> None:
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
