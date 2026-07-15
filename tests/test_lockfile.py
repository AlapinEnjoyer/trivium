"""Verify lockfile parsing, deterministic rendering, validation, and writes.

The cases cover missing and malformed files, mode and version checks, sorted
round trips, non-mutating serialization, and atomic replacement semantics.
"""

from pathlib import Path

import pytest

import skill_trivium.lockfile as lockfile_module
from skill_trivium.lockfile import LockfileError, load_lockfile, render_lockfile, write_lockfile_path
from skill_trivium.models import LockfileData, SkillLockEntry


def make_entry(name: str, *, optional: bool) -> SkillLockEntry:
    """Build a lock entry with optional metadata fields."""
    return SkillLockEntry(
        name=name,
        source_url="https://example.com/skills.git",
        commit_hash="abc123",
        content_hash="hash" if optional else None,
        skills_path="skills",
        install_path=f".agents/skills/{name}",
        description=f"{name} description",
        installed_at="2026-01-01T00:00:00Z",
        license="MIT" if optional else None,
        metadata={"owner": "team"} if optional else None,
    )


def test_render_and_load_lockfile_round_trip_is_sorted_and_non_mutating(tmp_path: Path) -> None:
    """Round-trip sorted lockfile data without mutating its input."""
    lockfile = LockfileData(
        meta={"version": 1, "custom": "preserved"},
        skills={"zeta": make_entry("zeta", optional=False), "alpha": make_entry("alpha", optional=True)},
    )

    rendered = render_lockfile(lockfile, meta_updates={"version": 1, "mode": "project"})
    path = tmp_path / "skills.lock"
    path.write_text(rendered, encoding="utf-8")
    loaded = load_lockfile(path, expected_mode="project")

    assert rendered.index("[skills.alpha]") < rendered.index("[skills.zeta]")
    assert lockfile.meta == {"version": 1, "custom": "preserved"}
    assert loaded.meta == {"version": 1, "custom": "preserved", "mode": "project"}
    assert loaded.skills["alpha"] == lockfile.skills["alpha"]
    assert loaded.skills["zeta"] == lockfile.skills["zeta"]
    assert "content_hash" not in loaded.skills["zeta"].to_toml_dict()


def test_load_lockfile_handles_missing_file_and_rejects_non_mapping_sections(tmp_path: Path) -> None:
    """Handle missing files and reject malformed lockfile sections."""
    missing = load_lockfile(tmp_path / "missing.lock")
    malformed_path = tmp_path / "malformed.lock"
    malformed_path.write_text('meta = "invalid"\nskills = ["invalid"]\n', encoding="utf-8")

    assert missing == LockfileData()
    with pytest.raises(LockfileError, match="'meta' must be a table"):
        load_lockfile(malformed_path)


@pytest.mark.parametrize(
    ("meta", "message"),
    [
        ('version = 2\nmode = "global"', "Unsupported lockfile version"),
        ('version = 1\nmode = "project"', "Lockfile mode mismatch"),
    ],
)
def test_load_lockfile_rejects_unsupported_version_and_wrong_mode(tmp_path: Path, meta: str, message: str) -> None:
    """Reject unsupported versions and installation mode mismatches."""
    path = tmp_path / "skills.lock"
    path.write_text(f"[meta]\n{meta}\n\n[skills]\n", encoding="utf-8")

    with pytest.raises(LockfileError, match=message):
        load_lockfile(path, expected_mode="global")


def test_write_lockfile_path_replaces_file_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Write a lockfile through one atomic replacement operation."""
    path = tmp_path / "skills.lock"
    path.write_text("old content", encoding="utf-8")
    replacements: list[tuple[Path, Path]] = []
    real_replace = lockfile_module.os.replace

    def recording_replace(source: Path, destination: Path) -> None:
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(lockfile_module.os, "replace", recording_replace)

    write_lockfile_path(
        path,
        LockfileData(),
        meta_updates={"version": 1, "mode": "global"},
    )

    assert len(replacements) == 1
    assert replacements[0][0].parent == path.parent
    assert replacements[0][1] == path
    assert load_lockfile(path, expected_mode="global") == LockfileData(meta={"version": 1, "mode": "global"})
