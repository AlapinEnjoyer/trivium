from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import skill_trivium.update as update_module
from skill_trivium.git import GitCloneError
from skill_trivium.models import InstallContext, LockfileData, SkillLockEntry, SourceUpdateResult, ValidationIssue
from skill_trivium.update import UpdateOutcome


def make_context(tmp_path: Path) -> InstallContext:
    return InstallContext(
        mode="project",
        base_dir=tmp_path,
        skills_dir=tmp_path / ".agents" / "skills",
        lockfile_path=tmp_path / "skills.lock",
        install_prefix=Path(".agents/skills"),
    )


def make_entry(name: str = "alpha") -> SkillLockEntry:
    return SkillLockEntry(
        name=name,
        source_url="https://git.example.com/private/repo.git",
        commit_hash="abc123",
        content_hash="hash",
        skills_path="skills",
        install_path=f".agents/skills/{name}",
        description="Alpha",
        installed_at="2026-01-01T00:00:00Z",
    )


@pytest.mark.parametrize(
    ("outcome", "dry_run", "expected"),
    [
        (
            UpdateOutcome(
                auth_failure=True,
                validation_issues=[ValidationIssue("alpha", "name", "invalid")],
                general_errors=True,
            ),
            True,
            5,
        ),
        (UpdateOutcome(validation_issues=[ValidationIssue("alpha", "name", "invalid")], general_errors=True), True, 2),
        (UpdateOutcome(general_errors=True, updated_names=["alpha"]), True, 1),
        (UpdateOutcome(updated_names=["alpha"]), True, 3),
        (UpdateOutcome(updated_names=["alpha"]), False, None),
    ],
)
def test_update_outcome_exit_code_precedence(outcome: UpdateOutcome, dry_run: bool, expected: int | None) -> None:
    assert outcome.exit_code(dry_run=dry_run) == expected


def test_apply_update_result_marks_normalization_rewrite_as_runtime_change() -> None:
    outcome = UpdateOutcome()

    update_module._apply_update_result(
        lockfile=LockfileData(),
        result=SourceUpdateResult(rewritten={"alpha"}),
        source_url="https://git.example.com/repo.git",
        dry_run=False,
        outcome=outcome,
    )

    assert outcome.runtime_changed is True
    assert outcome.updated_names == []


def test_update_source_group_preserves_authentication_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = make_entry()

    @contextmanager
    def failed_clone(_source_url: str) -> Iterator[tuple[Path, str]]:
        raise GitCloneError(
            source_url=entry.source_url,
            stderr="Authentication failed",
            auth_failure=True,
            guidance="Configure credentials.",
        )
        yield tmp_path, "unused"

    monkeypatch.setattr(update_module, "cloned_repo", failed_clone)

    result = update_module._update_source_group([entry], make_context(tmp_path), dry_run=False)

    assert result.auth_failure is True
    assert result.updated == {}
    assert result.errors == [f"{entry.source_url}: Authentication failed Configure credentials."]
