import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


@dataclass(frozen=True, slots=True)
class GitCloneError(Exception):
    source_url: str
    stderr: str
    auth_failure: bool
    guidance: str | None = None

    def __str__(self) -> str:
        return self.stderr


@contextmanager
def cloned_repo(source_url: str) -> Iterator[tuple[Path, str]]:
    with TemporaryDirectory(prefix="trivium-") as temp_dir:
        repo_path = Path(temp_dir) / "repo"
        clone_repository(source_url, repo_path)
        yield repo_path, current_commit(repo_path)


def clone_repository(source_url: str, destination: Path) -> None:
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--single-branch", "--quiet", source_url, str(destination)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return

    stderr = sanitize_git_error(result.stderr)
    auth_failure, guidance = classify_auth_failure(source_url, stderr)
    raise GitCloneError(source_url=source_url, stderr=stderr, auth_failure=auth_failure, guidance=guidance)


def current_commit(repo_path: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def sanitize_git_error(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return lines[0] if lines else "Git command failed."


def classify_auth_failure(source_url: str, stderr: str) -> tuple[bool, str | None]:
    lowered = stderr.lower()
    patterns = (
        "permission denied",
        "authentication failed",
        "repository not found",
        "could not read username",
        "fatal: could not read from remote repository",
    )
    if not any(pattern in lowered for pattern in patterns):
        return False, None

    if source_url.startswith(("git@", "ssh://")):
        return True, "Run `ssh -T git@github.com` to verify your key is loaded."
    if source_url.startswith(("https://", "http://")):
        return True, "Run `git config --global credential.helper` to check your credential store."
    return True, "Check that your git credentials are configured for this remote."
