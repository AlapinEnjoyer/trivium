"""Verify runtime mutation rollback and recovery preservation."""

from pathlib import Path

import pytest

from skill_trivium.models import InstallContext
from skill_trivium.mutation import RuntimeMutation


def test_runtime_mutation_restores_skill_tree_and_lockfile(tmp_path: Path) -> None:
    """Roll back both runtime artifacts when the mutation is not committed."""
    context = InstallContext(
        mode="project",
        base_dir=tmp_path,
        skills_dir=tmp_path / ".agents" / "skills",
        lockfile_path=tmp_path / "skills.lock",
        install_prefix=Path(".agents/skills"),
    )
    installed_document = context.install_path_for("alpha") / "SKILL.md"
    installed_document.parent.mkdir(parents=True)
    installed_document.write_text("previous", encoding="utf-8")
    context.lockfile_path.write_text("previous lockfile", encoding="utf-8")

    with RuntimeMutation(context):
        installed_document.write_text("changed", encoding="utf-8")
        context.lockfile_path.write_text("changed lockfile", encoding="utf-8")

    assert installed_document.read_text(encoding="utf-8") == "previous"
    assert context.lockfile_path.read_text(encoding="utf-8") == "previous lockfile"


def test_runtime_mutation_retains_backup_when_rollback_promotion_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave the backup recoverable when it cannot be promoted automatically."""
    context = InstallContext(
        mode="project",
        base_dir=tmp_path,
        skills_dir=tmp_path / ".agents" / "skills",
        lockfile_path=tmp_path / "skills.lock",
        install_prefix=Path(".agents/skills"),
    )
    installed_document = context.install_path_for("alpha") / "SKILL.md"
    installed_document.parent.mkdir(parents=True)
    installed_document.write_text("previous", encoding="utf-8")
    real_replace = Path.replace

    def fail_backup_promotion(source: Path, destination: Path) -> Path:
        if source.name == "skills" and source.parent.name.startswith(".trivium-mutation-"):
            raise OSError("simulated rollback failure")
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", fail_backup_promotion)

    with pytest.raises(OSError, match="simulated rollback failure"):
        with RuntimeMutation(context):
            installed_document.write_text("changed", encoding="utf-8")

    assert installed_document.read_text(encoding="utf-8") == "changed"
    transaction_dirs = list(context.skills_dir.parent.glob(".trivium-mutation-*"))
    assert len(transaction_dirs) == 1
    assert (transaction_dirs[0] / "skills" / "alpha" / "SKILL.md").read_text(encoding="utf-8") == "previous"
