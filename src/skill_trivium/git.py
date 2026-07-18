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
from urllib.parse import urlsplit


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
    """Clone a Git repository to a destination path.

    Args:
        source_url: Remote repository URL.
        destination: Local path to clone into.
        shallow: Whether to use a shallow clone (depth 1).
    """
    if source_url.lower().startswith(("http://", "https://")) and "@" in urlsplit(source_url).netloc:
        raise GitCloneError(source_url, "HTTP(S) Git repository URLs must not include credentials.", False)
    command = ["git", "clone", "--quiet"]
    if shallow:
        command.extend(["--depth", "1", "--single-branch"])
    command.extend(["--", source_url, str(destination)])
    stderr_chunks: list[str] = []
    show_stderr = sys.stdin.isatty() and sys.stderr.isatty()
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True) as process:
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
    raise GitCloneError(source_url, stderr, auth_failure, guidance)


def current_commit(repo_path: Path) -> str:
    """Return the full SHA of the current HEAD commit."""
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def checkout_revision(repo_path: Path, *, source_url: str, revision: str) -> None:
    """Check out a specific revision in an existing local clone.

    Args:
        repo_path: Path to the local repository.
        source_url: Remote URL (used only for error messages).
        revision: Git ref or SHA to check out.
    """
    if revision.startswith("-"):
        raise GitCheckoutError(source_url, revision, "Git revisions must not start with '-'.")
    result = subprocess.run(
        ["git", "checkout", "--quiet", revision, "--"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GitCheckoutError(source_url, revision, sanitize_git_error(result.stderr))


def sanitize_git_error(stderr: str) -> str:
    """Extract the first meaningful line from a Git error stream."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return lines[0] if lines else "Git command failed."


def classify_auth_failure(source_url: str, stderr: str) -> tuple[bool, str | None]:
    """Determine whether a Git error indicates an authentication failure.

    Args:
        source_url: The remote URL that was being accessed.
        stderr: Raw stderr output from the failed Git command.

    Returns:
        A tuple of (is_auth_failure, guidance_message_or_None).
    """
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
        return True, "Verify that your SSH key is loaded and authorized for this remote."
    if source_url.startswith(("https://", "http://")):
        return True, "Run `git config --global credential.helper` to check your credential store."
    return True, "Check that your git credentials are configured for this remote."
