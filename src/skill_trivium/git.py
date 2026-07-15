"""Clone repositories and resolve commits used as skill sources.

Git stderr is kept available for interactive credential prompts while errors
are normalized into domain exceptions with authentication guidance suitable
for CLI output.
"""

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


@dataclass(frozen=True, slots=True)
class GitCloneError(Exception):
    """Report a failed repository clone and possible authentication guidance."""

    source_url: str
    stderr: str
    auth_failure: bool
    guidance: str | None = None

    def __str__(self) -> str:
        """Return the sanitized Git error text."""
        return self.stderr


@dataclass(frozen=True, slots=True)
class GitCheckoutError(Exception):
    """Report a failed checkout of a requested repository revision."""

    source_url: str
    revision: str
    stderr: str

    def __str__(self) -> str:
        """Return the sanitized Git error text."""
        return self.stderr


@contextmanager
def cloned_repo(source_url: str) -> Iterator[tuple[Path, str]]:
    """Clone a repository temporarily and yield its path and commit hash.

    Args:
        source_url: Git URL to clone.

    Yields:
        A temporary repository path and the checked-out commit hash.

    Raises:
        GitCloneError: If cloning fails.
    """
    with TemporaryDirectory(prefix="trivium-") as temp_dir:
        repo_path = Path(temp_dir) / "repo"
        clone_repository(source_url, repo_path)
        yield repo_path, current_commit(repo_path)


@contextmanager
def cloned_repo_at_revision(source_url: str, revision: str) -> Iterator[Path]:
    """Clone a repository, check out a revision, and yield its temporary path.

    Args:
        source_url: Git URL to clone.
        revision: Commit, tag, or branch revision to check out.

    Yields:
        The temporary repository path at the requested revision.

    Raises:
        GitCloneError: If cloning fails.
        GitCheckoutError: If the revision cannot be checked out.
    """
    with TemporaryDirectory(prefix="trivium-") as temp_dir:
        repo_path = Path(temp_dir) / "repo"
        clone_repository(source_url, repo_path, shallow=False)
        checkout_revision(repo_path, source_url=source_url, revision=revision)
        yield repo_path


def clone_repository(source_url: str, destination: Path, *, shallow: bool = True) -> None:
    """Clone a repository and optionally limit the clone to its latest commit.

    Args:
        source_url: Git URL to clone.
        destination: Directory in which Git should create the repository.
        shallow: Whether to request a single-branch depth-one clone.

    Raises:
        GitCloneError: If Git exits unsuccessfully. Interactive stderr is also
            forwarded so credential prompts remain usable.
    """
    command = ["git", "clone", "--quiet"]
    if shallow:
        command.extend(["--depth", "1", "--single-branch"])
    command.extend([source_url, str(destination)])
    stderr_chunks: list[str] = []
    show_stderr = sys.stdin.isatty() and sys.stderr.isatty()
    with subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        assert process.stderr is not None
        while chunk := process.stderr.read(1):
            stderr_chunks.append(chunk)
            if show_stderr:
                sys.stderr.write(chunk)
                sys.stderr.flush()
        returncode = process.wait()

    if returncode == 0:
        return

    raw_stderr = "".join(stderr_chunks)
    stderr = sanitize_git_error(raw_stderr)
    auth_failure, guidance = classify_auth_failure(source_url, raw_stderr)
    raise GitCloneError(source_url=source_url, stderr=stderr, auth_failure=auth_failure, guidance=guidance)


def current_commit(repo_path: Path) -> str:
    """Read the full commit hash currently checked out in a repository.

    Args:
        repo_path: Existing local Git repository path.

    Returns:
        The trimmed output of ``git rev-parse HEAD``.

    Raises:
        subprocess.CalledProcessError: If the repository has no readable HEAD.
    """
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def checkout_revision(repo_path: Path, *, source_url: str, revision: str) -> None:
    """Check out a revision or raise a sanitized checkout error.

    Args:
        repo_path: Existing local Git repository path.
        source_url: Original source URL used for diagnostic context.
        revision: Commit, tag, or branch revision to check out.

    Raises:
        GitCheckoutError: If Git cannot resolve or check out the revision.
    """
    result = subprocess.run(
        ["git", "checkout", "--quiet", revision],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return
    raise GitCheckoutError(source_url=source_url, revision=revision, stderr=sanitize_git_error(result.stderr))


def sanitize_git_error(stderr: str) -> str:
    """Return the first useful non-empty line from Git stderr."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return lines[0] if lines else "Git command failed."


def classify_auth_failure(source_url: str, stderr: str) -> tuple[bool, str | None]:
    """Classify Git stderr and return authentication guidance when applicable."""
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
