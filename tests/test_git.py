import io
import sys
from pathlib import Path

import pytest

import skill_trivium.git as git_module
from skill_trivium.git import GitCheckoutError, GitCloneError, checkout_revision, clone_repository


class InteractiveStream(io.StringIO):
    def isatty(self) -> bool:
        return True


class FailedCloneProcess:
    def __init__(self, stderr: str, returncode: int = 128) -> None:
        self.stderr = io.StringIO(stderr)
        self.returncode = returncode

    def __enter__(self) -> "FailedCloneProcess":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        pass

    def wait(self) -> int:
        return self.returncode


def test_clone_repository_displays_git_credential_prompt_in_interactive_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = "Username for 'https://git.example.com': "
    terminal_output = InteractiveStream()
    monkeypatch.setattr(sys, "stdin", InteractiveStream())
    monkeypatch.setattr(sys, "stderr", terminal_output)
    monkeypatch.setattr(git_module.subprocess, "Popen", lambda *_args, **_kwargs: FailedCloneProcess(prompt))

    with pytest.raises(GitCloneError) as exc_info:
        clone_repository("https://git.example.com/private/repo.git", tmp_path / "repo")

    assert terminal_output.getvalue() == prompt
    assert exc_info.value.stderr == prompt.strip()


def test_clone_repository_keeps_stderr_hidden_when_noninteractive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal_output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    monkeypatch.setattr(sys, "stderr", terminal_output)
    monkeypatch.setattr(
        git_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FailedCloneProcess("fatal: Authentication failed\n"),
    )

    with pytest.raises(GitCloneError) as exc_info:
        clone_repository("https://git.example.com/private/repo.git", tmp_path / "repo")

    assert terminal_output.getvalue() == ""
    assert exc_info.value.auth_failure is True


def test_clone_repository_classifies_auth_failure_from_later_stderr_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stderr = "remote: Access denied\nfatal: Authentication failed for repository\n"
    monkeypatch.setattr(
        git_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FailedCloneProcess(stderr),
    )

    with pytest.raises(GitCloneError) as exc_info:
        clone_repository("https://git.example.com/private/repo.git", tmp_path / "repo")

    assert exc_info.value.stderr == "remote: Access denied"
    assert exc_info.value.auth_failure is True
    assert exc_info.value.guidance is not None


def test_clone_repository_builds_shallow_clone_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    invocation: dict[str, object] = {}

    def fake_popen(command: list[str], **kwargs: object) -> FailedCloneProcess:
        invocation["command"] = command
        invocation.update(kwargs)
        return FailedCloneProcess("", returncode=0)

    monkeypatch.setattr(git_module.subprocess, "Popen", fake_popen)
    destination = tmp_path / "repo"

    clone_repository("https://git.example.com/repo.git", destination)

    assert invocation["command"] == [
        "git",
        "clone",
        "--quiet",
        "--depth",
        "1",
        "--single-branch",
        "https://git.example.com/repo.git",
        str(destination),
    ]
    assert invocation["stdout"] is git_module.subprocess.DEVNULL
    assert invocation["stderr"] is git_module.subprocess.PIPE
    assert invocation["text"] is True


def test_checkout_revision_raises_sanitized_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: object) -> object:
        assert command == ["git", "checkout", "--quiet", "missing"]
        assert kwargs["cwd"] == tmp_path
        return type("Result", (), {"returncode": 1, "stderr": "fatal: bad revision\nmore detail\n"})()

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)

    with pytest.raises(GitCheckoutError) as exc_info:
        checkout_revision(tmp_path, source_url="https://git.example.com/repo.git", revision="missing")

    assert exc_info.value.source_url == "https://git.example.com/repo.git"
    assert exc_info.value.revision == "missing"
    assert exc_info.value.stderr == "fatal: bad revision"
