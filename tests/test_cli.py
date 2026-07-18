"""Exercise CLI commands through Typer's runner and temporary Git projects.

These tests cover command parsing, project and global installations, conflict
handling, lockfile behavior, updates, and environment lifecycle operations.
"""

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import skill_trivium.cli as cli_module
from skill_trivium.cli import app
from skill_trivium.lockfile import load_lockfile
from skill_trivium.skills import parse_skill_document

runner = CliRunner()


def test_root_without_args_shows_help() -> None:
    """Show root help when no command is supplied."""
    result = runner.invoke(app, [])

    assert result.exit_code == 2, result.output
    assert "Usage:" in result.output
    assert "Commands" in result.output


def test_short_help_flag_works_on_root_and_subcommand() -> None:
    """Support the short help flag on root and subcommands."""
    root_result = runner.invoke(app, ["-h"])
    env_result = runner.invoke(app, ["env", "-h"])

    assert root_result.exit_code == 0, root_result.output
    assert env_result.exit_code == 0, env_result.output
    assert "Usage:" in root_result.output
    assert "Usage:" in env_result.output


def test_version_flag_shows_version() -> None:
    """Print the package version from the root callback."""
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("trivium ")


def test_add_installs_all_valid_skills_and_writes_lockfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Install all valid repository skills and record them in the lockfile."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "alpha-skill": skill_markdown("alpha-skill", "Alpha skill description"),
            "beta-skill": skill_markdown(
                "beta-skill",
                "Beta skill description",
                metadata={"author": "example-org", "version": "1.0"},
            ),
        },
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["add", str(remote_repo), "--all"])

    assert result.exit_code == 0, result.output
    assert "Add Summary" in result.output
    assert (project_root / ".agents" / "skills" / "alpha-skill" / "SKILL.md").is_file()
    assert (project_root / ".agents" / "skills" / "beta-skill" / "SKILL.md").is_file()

    lockfile = load_lockfile(project_root / "skills.lock")
    assert sorted(lockfile.skills) == ["alpha-skill", "beta-skill"]
    assert lockfile.meta["version"] == 1
    assert lockfile.meta["mode"] == "project"
    assert lockfile.skills["alpha-skill"].skills_path == "."
    assert lockfile.skills["alpha-skill"].install_path == ".agents/skills/alpha-skill"
    assert len(lockfile.skills["alpha-skill"].commit_hash) == 40
    assert lockfile.skills["beta-skill"].metadata == {"author": "example-org", "version": "1.0"}


def test_add_dry_run_previews_install_without_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Preview an add without writing runtime or lockfile data."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"alpha-skill": skill_markdown("alpha-skill", "Alpha skill description")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["add", str(remote_repo), "--all", "--dry-run"])

    assert result.exit_code == 3, result.output
    assert "Dry Run" in result.output
    assert "Would install: alpha-skill" in result.output
    assert not (project_root / ".agents" / "skills" / "alpha-skill").exists()
    assert not (project_root / "skills.lock").exists()


def test_add_supports_explicit_path_and_named_skill_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Select named skills from an explicit repository subdirectory."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "alpha-skill": skill_markdown("alpha-skill", "Alpha"),
            "beta-skill": skill_markdown("beta-skill", "Beta"),
        },
        container="skills",
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["add", str(remote_repo), "--skills", "beta-skill", "--path", "skills"])

    assert result.exit_code == 0, result.output
    assert not (project_root / ".agents" / "skills" / "alpha-skill").exists()
    assert (project_root / ".agents" / "skills" / "beta-skill" / "SKILL.md").is_file()

    lockfile = load_lockfile(project_root / "skills.lock")
    assert sorted(lockfile.skills) == ["beta-skill"]
    assert lockfile.skills["beta-skill"].skills_path == "skills"


def test_add_skips_invalid_skill_but_installs_valid_ones(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Install valid skills while reporting invalid skills."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "good-skill": skill_markdown("good-skill", "Good skill"),
            "bad-skill": "---\nname: bad-skill\n---\n",
        },
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["add", str(remote_repo), "--all"])

    assert result.exit_code == 2
    assert (project_root / ".agents" / "skills" / "good-skill" / "SKILL.md").is_file()
    assert not (project_root / ".agents" / "skills" / "bad-skill").exists()
    assert "Validation Failed: bad-skill" in result.output


def test_add_ignore_validation_installs_invalid_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Install invalid skills when validation is explicitly ignored."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "good-skill": skill_markdown("good-skill", "Good skill"),
            "bad-skill": "---\nname: bad-skill\n---\n",
        },
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["add", str(remote_repo), "--all", "--ignore-validation"])

    assert result.exit_code == 0, result.output
    assert "Add Summary" in result.output
    assert (project_root / ".agents" / "skills" / "good-skill" / "SKILL.md").is_file()
    assert (project_root / ".agents" / "skills" / "bad-skill" / "SKILL.md").is_file()


def test_add_ignore_validation_with_specific_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply ignored validation to specifically selected skills."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "valid-skill": skill_markdown("valid-skill", "Valid skill"),
            "invalid-skill": "---\nname: invalid-skill\n---\n",
        },
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["add", str(remote_repo), "--skills", "invalid-skill", "--ignore-validation"])

    assert result.exit_code == 0, result.output
    assert (project_root / ".agents" / "skills" / "invalid-skill" / "SKILL.md").is_file()
    assert not (project_root / ".agents" / "skills" / "valid-skill").exists()


def test_add_ignore_validation_no_exit_code_two(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid validation exit code two when ignoring validation."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"bad-skill": "---\nname: bad-skill\n---\n"},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["add", str(remote_repo), "--all", "--ignore-validation"])

    assert result.exit_code == 0, result.output
    assert "Validation Failed" not in result.output


def test_add_ignore_validation_does_not_hide_parse_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep parseability structural even when specification checks are ignored."""
    remote_repo = create_git_skill_repo(tmp_path / "remote", {"broken": "---\nname: [\n---\n"})
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["add", str(remote_repo), "--all", "--ignore-validation"])

    assert result.exit_code == 2
    assert "Validation Failed: broken" in result.output


def test_add_ignore_validation_rejects_unsafe_install_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Continue rejecting unsafe install paths despite ignored validation."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"unsafe-skill": "---\nname: ../escape\ndescription: Unsafe\n---\n"},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["add", str(remote_repo), "--all", "--ignore-validation"])

    assert result.exit_code == 2
    assert "Validation Failed: unsafe-skill" in result.output
    assert "may only use lowercase" in result.output
    assert not (project_root / ".agents" / "escape").exists()
    assert not (project_root / "skills.lock").exists()


def test_add_ignore_validation_rejects_directory_name_mismatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep source identity structural when validation is otherwise ignored."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "first-skill": skill_markdown("shared-name", "First"),
            "second-skill": skill_markdown("shared-name", "Second"),
        },
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["add", str(remote_repo), "--all", "--ignore-validation"])

    assert result.exit_code == 2
    assert "Validation Failed: first-skill" in result.output
    assert "must match the parent directory" in result.output
    assert not (project_root / ".agents" / "skills" / "shared-name").exists()


def test_add_normalizes_yaml_list_allowed_tools_in_installed_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normalize YAML list allowed-tools metadata during installation."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "learn": "\n".join(
                [
                    "---",
                    "name: learn",
                    "preamble-tier: 2",
                    'version: "1.0.0"',
                    "description: |",
                    "  Manage project learnings.",
                    "allowed-tools:",
                    "  - Bash",
                    "  - Read",
                    "  - Write",
                    "---",
                    "",
                    "## Instructions",
                    "",
                    "Use this skill carefully.",
                ]
            )
        },
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["add", str(remote_repo), "--all"])

    assert result.exit_code == 0, result.output
    assert "Conversion Warning: learn" in result.output

    installed_path = project_root / ".agents" / "skills" / "learn" / "SKILL.md"
    frontmatter, _ = parse_skill_document(installed_path)
    assert frontmatter["preamble-tier"] == 2
    assert frontmatter["version"] == "1.0.0"
    assert frontmatter["allowed-tools"] == "Bash Read Write"
    assert not isinstance(frontmatter["allowed-tools"], list)

    lockfile = load_lockfile(project_root / "skills.lock")
    assert lockfile.skills["learn"].allowed_tools == "Bash Read Write"


def test_add_normalizes_yaml_scalar_metadata_values_in_installed_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normalize scalar metadata values during installation."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "capture-api-response-test-fixture": "\n".join(
                [
                    "---",
                    "name: capture-api-response-test-fixture",
                    "description: |",
                    "  Capture API response test fixture.",
                    "metadata:",
                    "  internal: true",
                    "  retries: 2",
                    "  ratio: 1.5",
                    "  retired: null",
                    "---",
                    "",
                    "## Instructions",
                    "",
                    "Use this skill carefully.",
                ]
            )
        },
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["add", str(remote_repo), "--all"])

    assert result.exit_code == 0, result.output
    assert "Conversion Warning: capture-api-response-test-fixture" in result.output

    installed_path = project_root / ".agents" / "skills" / "capture-api-response-test-fixture" / "SKILL.md"
    frontmatter, _ = parse_skill_document(installed_path)
    assert frontmatter["metadata"] == {
        "internal": "true",
        "retries": "2",
        "ratio": "1.5",
        "retired": "null",
    }

    lockfile = load_lockfile(project_root / "skills.lock")
    assert lockfile.skills["capture-api-response-test-fixture"].metadata == {
        "internal": "true",
        "retries": "2",
        "ratio": "1.5",
        "retired": "null",
    }


def test_add_conflict_without_yes_exits_four_in_non_interactive_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require explicit conflict resolution in non-interactive mode."""
    existing_repo = create_git_skill_repo(
        tmp_path / "existing",
        {"shared-skill": skill_markdown("shared-skill", "Existing source")},
    )
    incoming_repo = create_git_skill_repo(
        tmp_path / "incoming",
        {"shared-skill": skill_markdown("shared-skill", "Incoming source")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    first = runner.invoke(app, ["add", str(existing_repo), "--all"])
    assert first.exit_code == 0, first.output

    result = runner.invoke(app, ["add", str(incoming_repo), "--all"])

    assert result.exit_code == 4
    assert "Conflict: shared-skill" in result.output


def test_add_conflict_with_yes_replaces_existing_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace an existing skill when the yes flag is supplied."""
    existing_repo = create_git_skill_repo(
        tmp_path / "existing",
        {"shared-skill": skill_markdown("shared-skill", "Existing source")},
    )
    incoming_repo = create_git_skill_repo(
        tmp_path / "incoming",
        {"shared-skill": skill_markdown("shared-skill", "Incoming source")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    first = runner.invoke(app, ["add", str(existing_repo), "--all"])
    assert first.exit_code == 0, first.output

    result = runner.invoke(app, ["add", str(incoming_repo), "--all", "--yes"])

    assert result.exit_code == 0, result.output
    lockfile = load_lockfile(project_root / "skills.lock")
    assert lockfile.skills["shared-skill"].source_url == str(incoming_repo)


def test_add_stops_progress_before_interactive_conflict_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop progress rendering before an interactive conflict prompt."""
    existing_repo = create_git_skill_repo(
        tmp_path / "existing",
        {"shared-skill": skill_markdown("shared-skill", "Existing source")},
    )
    incoming_repo = create_git_skill_repo(
        tmp_path / "incoming",
        {"shared-skill": skill_markdown("shared-skill", "Incoming source")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    first = runner.invoke(app, ["add", str(existing_repo), "--all"])
    assert first.exit_code == 0, first.output

    class FakeProgress:
        def __init__(self) -> None:
            self.stopped = False

        def __enter__(self) -> "FakeProgress":
            return self

        def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
            return None

        def add_task(self, description: str, total: object = None) -> int:
            return 1

        def update(self, task_id: int, completed: int) -> None:
            return None

        def stop(self) -> None:
            self.stopped = True

    progress = FakeProgress()

    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli_module, "progress_bar", lambda: progress)

    def fake_select(message: str, choices: list[str]) -> object:
        assert progress.stopped
        assert message == "Resolve conflict for 'shared-skill'"
        assert choices == ["Keep existing", "Replace with new", "Skip"]

        class Prompt:
            def ask(self) -> str:
                return "Keep existing"

        return Prompt()

    monkeypatch.setattr(cli_module.questionary, "select", fake_select)

    result = runner.invoke(app, ["add", str(incoming_repo), "--all"])

    assert result.exit_code == 0, result.output
    assert progress.stopped
    assert "Skipped: shared-skill (kept existing)" in result.output


def test_add_same_source_readd_is_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat an unchanged same-source re-add as a no-op."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"repeat-skill": skill_markdown("repeat-skill", "Repeat source")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    first = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert first.exit_code == 0, first.output
    initial_commit = load_lockfile(project_root / "skills.lock").skills["repeat-skill"].commit_hash

    second = runner.invoke(app, ["add", str(remote_repo), "--all"])

    assert second.exit_code == 0, second.output
    assert "already installed from the same source" in second.output
    assert load_lockfile(project_root / "skills.lock").skills["repeat-skill"].commit_hash == initial_commit


def test_add_same_source_readd_does_not_install_changed_remote_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep same-source re-adds from bypassing update and stale lock metadata."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "learn": "\n".join(
                [
                    "---",
                    "name: learn",
                    "preamble-tier: 2",
                    'version: "1.0.0"',
                    "description: |",
                    "  Manage project learnings.",
                    "allowed-tools:",
                    "  - Bash",
                    "  - Read",
                    "---",
                    "",
                    "## Instructions",
                    "",
                    "Use this skill carefully.",
                ]
            )
        },
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    first = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert first.exit_code == 0, first.output

    installed_path = project_root / ".agents" / "skills" / "learn" / "SKILL.md"
    installed_content = installed_path.read_text(encoding="utf-8")
    initial_entry = load_lockfile(project_root / "skills.lock").skills["learn"]
    write_skill(
        remote_repo / "learn" / "SKILL.md",
        "\n".join(
            [
                "---",
                "name: learn",
                "preamble-tier: 2",
                'version: "1.0.0"',
                "description: |",
                "  Manage project learnings.",
                "allowed-tools:",
                "  - Bash",
                "  - Read",
                "---",
                "",
                "## Instructions",
                "",
                "Changed remote instructions.",
            ]
        ),
    )
    git_commit(remote_repo, "Change learn skill")

    second = runner.invoke(app, ["add", str(remote_repo), "--all"])

    assert second.exit_code == 0, second.output
    assert "already installed from the same source" in second.output
    assert installed_path.read_text(encoding="utf-8") == installed_content
    assert load_lockfile(project_root / "skills.lock").skills["learn"] == initial_entry


def test_list_shows_installed_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """List installed skills in project mode."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"alpha-skill": skill_markdown("alpha-skill", "Alpha description")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)
    runner.invoke(app, ["add", str(remote_repo), "--all"])

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0, result.output
    assert "alpha-skill" in result.output


def test_list_without_installed_skills_does_not_create_lockfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Avoid creating a lockfile when listing an empty installation."""
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0, result.output
    assert "No installed skills" in result.output
    assert not (project_root / "skills.lock").exists()


def test_ls_is_not_supported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject the unsupported ls alias."""
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["ls"])

    assert result.exit_code == 2
    assert "No such command 'ls'" in result.output


def test_list_json_outputs_lockfile_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Render the lockfile as JSON for list --json."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"alpha-skill": skill_markdown("alpha-skill", "Alpha description")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)
    runner.invoke(app, ["add", str(remote_repo), "--all"])

    result = runner.invoke(app, ["list", "--json"])

    assert result.exit_code == 0, result.output
    assert '"alpha-skill"' in result.output
    assert '"source_url"' in result.output


def test_info_renders_lockfile_metadata_and_markdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Render installed metadata and markdown through the info command."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "info-skill": skill_markdown(
                "info-skill",
                "Info description",
                license="Apache-2.0",
                compatibility="Requires Python 3.13+ and uv",
                allowed_tools="bash read",
                metadata={"author": "example-org"},
                body="## Instructions\n\nUse this skill for information-heavy tasks.\n",
            )
        },
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)
    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output

    result = runner.invoke(app, ["info", "info-skill"])

    assert result.exit_code == 0, result.output
    assert "Source URL:" in result.output
    assert "License: Apache-2.0" in result.output
    assert "Allowed Tools: bash read" in result.output
    assert "Use this skill for information-heavy tasks." in result.output


def test_remove_named_skill_and_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove named skills and all skills from an installation."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "alpha-skill": skill_markdown("alpha-skill", "Alpha"),
            "beta-skill": skill_markdown("beta-skill", "Beta"),
        },
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)
    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output

    named_remove = runner.invoke(app, ["remove", "alpha-skill", "--yes"])
    assert named_remove.exit_code == 0, named_remove.output
    assert not (project_root / ".agents" / "skills" / "alpha-skill").exists()
    assert (project_root / ".agents" / "skills" / "beta-skill").exists()

    remove_all = runner.invoke(app, ["remove", "--all", "--yes"])
    assert remove_all.exit_code == 0, remove_all.output
    assert not (project_root / ".agents" / "skills" / "beta-skill").exists()
    assert load_lockfile(project_root / "skills.lock").skills == {}


def test_remove_requires_yes_when_noninteractive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not wait on a hidden prompt when the terminal is non-interactive."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"alpha-skill": skill_markdown("alpha-skill", "Alpha")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)
    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output

    result = runner.invoke(app, ["remove", "--all"], input="y\n")

    assert result.exit_code == 4
    assert "requires --yes" in result.output
    assert (project_root / ".agents" / "skills" / "alpha-skill").exists()


def test_remove_noninteractive_eof_requires_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat EOF as unavailable confirmation rather than successful cancellation."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"alpha-skill": skill_markdown("alpha-skill", "Alpha")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)
    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output

    result = runner.invoke(app, ["remove", "--all"], input="")

    assert result.exit_code == 4
    assert "Confirmation Required" in result.output
    assert (project_root / ".agents" / "skills" / "alpha-skill").exists()


def test_update_refreshes_skill_from_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refresh a changed skill from its remote repository."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"alpha-skill": skill_markdown("alpha-skill", "Original description")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output
    original_commit = load_lockfile(project_root / "skills.lock").skills["alpha-skill"].commit_hash

    write_skill(remote_repo / "alpha-skill" / "SKILL.md", skill_markdown("alpha-skill", "Updated description"))
    git_commit(remote_repo, "Update alpha skill")

    result = runner.invoke(app, ["update", "alpha-skill"])

    assert result.exit_code == 0, result.output
    assert "Updated skill 'alpha-skill'." in result.output
    updated_entry = load_lockfile(project_root / "skills.lock").skills["alpha-skill"]
    assert updated_entry.commit_hash != original_commit
    assert updated_entry.description == "Updated description"
    assert "Updated description" in (project_root / ".agents" / "skills" / "alpha-skill" / "SKILL.md").read_text(
        encoding="utf-8"
    )


def test_update_dry_run_exits_three_when_changes_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return the dry-run change exit code without writing changes."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"alpha-skill": skill_markdown("alpha-skill", "Original description")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)
    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output

    write_skill(remote_repo / "alpha-skill" / "SKILL.md", skill_markdown("alpha-skill", "Updated description"))
    git_commit(remote_repo, "Update alpha skill")

    result = runner.invoke(app, ["update", "--dry-run"])

    assert result.exit_code == 3
    assert "Would update skill 'alpha-skill'." in result.output
    entry = load_lockfile(project_root / "skills.lock").skills["alpha-skill"]
    assert entry.description == "Original description"


def test_update_without_installed_skills_does_not_create_lockfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Avoid creating a lockfile when updating an empty installation."""
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert "No Skills Installed" in result.output
    assert not (project_root / "skills.lock").exists()


def test_explicit_missing_targets_fail_on_empty_installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep explicit target status independent of unrelated installed skills."""
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    assert runner.invoke(app, ["update", "missing-skill"]).exit_code == 2
    assert runner.invoke(app, ["remove", "missing-skill", "--yes"]).exit_code == 2


def test_update_only_reinstalls_changed_skill_from_shared_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reinstall only the changed skill from a shared source repository."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "alpha-skill": skill_markdown("alpha-skill", "Alpha v1"),
            "beta-skill": skill_markdown("beta-skill", "Beta v1"),
        },
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output

    beta_installed = project_root / ".agents" / "skills" / "beta-skill" / "SKILL.md"
    sentinel = "\n<!-- local beta marker -->\n"
    beta_installed.write_text(beta_installed.read_text(encoding="utf-8") + sentinel, encoding="utf-8")

    write_skill(remote_repo / "alpha-skill" / "SKILL.md", skill_markdown("alpha-skill", "Alpha v2"))
    git_commit(remote_repo, "Update alpha only")

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert "Updated skill 'alpha-skill'." in result.output
    assert "Updated skill 'beta-skill'." not in result.output
    assert sentinel in beta_installed.read_text(encoding="utf-8")
    assert "Alpha v2" in (project_root / ".agents" / "skills" / "alpha-skill" / "SKILL.md").read_text(encoding="utf-8")


def test_update_does_not_rewrite_local_edits_for_unchanged_normalized_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preserve local edits when only the source representation needs normalization."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"alpha": "---\nname: alpha\ndescription: Alpha\nallowed-tools:\n  - Bash\n---\n"},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)
    assert runner.invoke(app, ["add", str(remote_repo), "--all"]).exit_code == 0
    installed = project_root / ".agents" / "skills" / "alpha" / "SKILL.md"
    installed.write_text(installed.read_text(encoding="utf-8") + "\n<!-- local -->\n", encoding="utf-8")

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert "<!-- local -->" in installed.read_text(encoding="utf-8")


def test_update_without_content_hash_only_refreshes_lockfile_for_unchanged_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refresh legacy lock metadata without reinstalling unchanged content."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"alpha-skill": skill_markdown("alpha-skill", "Original description")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output

    lockfile_path = project_root / "skills.lock"
    original_lockfile = lockfile_path.read_text(encoding="utf-8")
    assert "content_hash" in original_lockfile
    without_hash = "\n".join(line for line in original_lockfile.splitlines() if "content_hash" not in line) + "\n"
    lockfile_path.write_text(without_hash, encoding="utf-8")

    installed_path = project_root / ".agents" / "skills" / "alpha-skill" / "SKILL.md"
    before = installed_path.read_text(encoding="utf-8")

    dry_run = runner.invoke(app, ["update", "--dry-run"])

    assert dry_run.exit_code == 3
    assert "Would refresh lockfile metadata" in dry_run.output
    assert "content_hash" not in lockfile_path.read_text(encoding="utf-8")

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert "Up To Date" in result.output
    assert "Updated skill 'alpha-skill'." not in result.output
    assert installed_path.read_text(encoding="utf-8") == before
    assert "content_hash" in lockfile_path.read_text(encoding="utf-8")


def test_update_warns_when_skill_path_or_skill_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Report missing source paths and skill directories as warnings."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"alpha-skill": skill_markdown("alpha-skill", "Alpha description")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)
    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output

    shutil_rmtree(remote_repo / "alpha-skill")
    git_commit(remote_repo, "Remove alpha skill")

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert "Update Warning: alpha-skill" in result.output
    assert "re-run `trv add".lower() in result.output.lower()


def test_add_same_source_readd_does_not_repair_locally_modified_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave local modifications untouched during a same-source re-add."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "capture-api-response-test-fixture": "\n".join(
                [
                    "---",
                    "name: capture-api-response-test-fixture",
                    "description: |",
                    "  Capture API response test fixture.",
                    "metadata:",
                    "  author: example-org",
                    "  internal: true",
                    "---",
                    "",
                    "## Instructions",
                    "",
                    "Use this skill carefully.",
                ]
            )
        },
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output

    installed_path = project_root / ".agents" / "skills" / "capture-api-response-test-fixture" / "SKILL.md"
    write_skill(
        installed_path,
        "\n".join(
            [
                "---",
                "name: capture-api-response-test-fixture",
                "description: |",
                "  Capture API response test fixture.",
                "metadata:",
                "  author: example-org",
                "  internal: true",
                "---",
                "",
                "## Instructions",
                "",
                "Use this skill carefully.",
            ]
        ),
    )

    result = runner.invoke(app, ["add", str(remote_repo), "--all"])

    assert result.exit_code == 0, result.output
    assert "Skipped: capture-api-response-test-fixture" in result.output
    frontmatter, _ = parse_skill_document(installed_path)
    assert frontmatter["metadata"] == {"author": "example-org", "internal": True}


def test_global_mode_uses_home_agents_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Install global skills beneath the configured home agents directory."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"global-skill": skill_markdown("global-skill", "Global description")},
    )
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["add", str(remote_repo), "--all", "--global"])

    assert result.exit_code == 0, result.output
    assert (fake_home / ".agents" / "skills" / "global-skill" / "SKILL.md").is_file()
    lockfile = load_lockfile(fake_home / ".agents" / "skills.lock")
    assert lockfile.meta["mode"] == "global"
    assert lockfile.skills["global-skill"].install_path == "skills/global-skill"


def test_global_mode_merges_sequential_adds_from_multiple_repositories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Merge sequential global adds from multiple repositories."""
    first_repo = create_git_skill_repo(
        tmp_path / "first-remote",
        {"alpha-skill": skill_markdown("alpha-skill", "Alpha description")},
    )
    second_repo = create_git_skill_repo(
        tmp_path / "second-remote",
        {"beta-skill": skill_markdown("beta-skill", "Beta description")},
    )
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(tmp_path)

    first_result = runner.invoke(app, ["add", str(first_repo), "--all", "--global"])
    second_result = runner.invoke(app, ["add", str(second_repo), "--all", "--global"])

    assert first_result.exit_code == 0, first_result.output
    assert second_result.exit_code == 0, second_result.output
    lockfile = load_lockfile(fake_home / ".agents" / "skills.lock", expected_mode="global")
    assert sorted(lockfile.skills) == ["alpha-skill", "beta-skill"]
    assert (fake_home / ".agents" / "skills" / "alpha-skill" / "SKILL.md").is_file()
    assert (fake_home / ".agents" / "skills" / "beta-skill" / "SKILL.md").is_file()


def test_global_add_refuses_to_replace_untracked_skill_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse replacing an untracked global skill directory."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"manual-skill": skill_markdown("manual-skill", "Remote description")},
    )
    fake_home = tmp_path / "home"
    manual_dir = fake_home / ".agents" / "skills" / "manual-skill"
    manual_dir.mkdir(parents=True)
    marker = manual_dir / "local-work.txt"
    marker.write_text("preserve me", encoding="utf-8")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["add", str(remote_repo), "--all", "--global"])

    assert result.exit_code == 4
    assert "Untracked Skill: manual-skill" in result.output
    assert marker.read_text(encoding="utf-8") == "preserve me"
    assert not (fake_home / ".agents" / "skills.lock").exists()


def test_local_mode_outside_git_repo_uses_current_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the current directory as the project context outside Git."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"local-skill": skill_markdown("local-skill", "Local description")},
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(workspace)

    result = runner.invoke(app, ["add", str(remote_repo), "--all"])

    assert result.exit_code == 0, result.output
    assert (workspace / ".agents" / "skills" / "local-skill" / "SKILL.md").is_file()
    assert (workspace / "skills.lock").is_file()
    assert not (fake_home / ".agents" / "skills" / "local-skill").exists()
    lockfile = load_lockfile(workspace / "skills.lock")
    assert lockfile.meta["mode"] == "project"
    assert lockfile.skills["local-skill"].install_path == ".agents/skills/local-skill"


def test_add_auth_failure_uses_exit_code_five(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Map Git authentication failures to exit code five."""
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["add", "https://github.com/example/private.git", "--all"])

    assert result.exit_code == 5
    assert "Git Clone Failed" in result.output
    assert "credential.helper" in result.output


def test_env_create_list_and_info_use_project_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Create and inspect a canonical project environment manifest."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"pdf": skill_markdown("pdf", "PDF skill")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(project_root)
    assert runner.invoke(app, ["add", str(remote_repo), "--all"]).exit_code == 0

    create_result = runner.invoke(app, ["env", "create", "office"])
    list_result = runner.invoke(app, ["env", "list"])
    info_result = runner.invoke(app, ["env", "info", "office"])

    assert create_result.exit_code == 0, create_result.output
    assert list_result.exit_code == 0, list_result.output
    assert info_result.exit_code == 0, info_result.output
    assert "office" in list_result.output
    assert "Scope: project" in info_result.output
    manifest_path = project_root / ".agents" / "environments" / "office.lock"
    assert sorted(load_lockfile(manifest_path, expected_mode="project").skills) == ["pdf"]


def test_global_environment_activates_into_current_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Activate one reusable global manifest from another repository."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"pdf": skill_markdown("pdf", "PDF skill")},
    )
    source_project = make_project_root(tmp_path / "source")
    target_project = make_project_root(tmp_path / "target")
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(source_project)
    assert runner.invoke(app, ["add", str(remote_repo), "--all"]).exit_code == 0
    assert runner.invoke(app, ["env", "create", "office", "--global"]).exit_code == 0

    monkeypatch.chdir(target_project)
    result = runner.invoke(app, ["env", "activate", "office", "--global"])

    assert result.exit_code == 0, result.output
    assert (target_project / ".agents" / "skills" / "pdf" / "SKILL.md").is_file()
    assert not (fake_home / ".agents").exists()
    info_result = runner.invoke(app, ["env", "info"])
    assert info_result.exit_code == 0, info_result.output
    assert "Scope: global" in info_result.output


def test_env_activation_and_deactivation_restore_previous_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Materialize a manifest and later restore the preceding runtime."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "pdf": skill_markdown("pdf", "PDF skill"),
            "word": skill_markdown("word", "Word skill"),
        },
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(project_root)
    assert runner.invoke(app, ["add", str(remote_repo), "--skills", "pdf"]).exit_code == 0
    assert runner.invoke(app, ["env", "create", "office"]).exit_code == 0
    assert runner.invoke(app, ["remove", "pdf", "--yes"]).exit_code == 0
    assert runner.invoke(app, ["add", str(remote_repo), "--skills", "word"]).exit_code == 0

    activate_result = runner.invoke(app, ["env", "activate", "office"])
    deactivate_result = runner.invoke(app, ["env", "deactivate"])

    assert activate_result.exit_code == 0, activate_result.output
    assert deactivate_result.exit_code == 0, deactivate_result.output
    assert (project_root / ".agents" / "skills" / "word").is_dir()
    assert not (project_root / ".agents" / "skills" / "pdf").exists()


def test_add_automatically_updates_active_environment_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist package mutations directly to the active manifest."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "pdf": skill_markdown("pdf", "PDF skill"),
            "word": skill_markdown("word", "Word skill"),
        },
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(project_root)
    assert runner.invoke(app, ["add", str(remote_repo), "--skills", "pdf"]).exit_code == 0
    assert runner.invoke(app, ["env", "create", "office"]).exit_code == 0
    assert runner.invoke(app, ["env", "activate", "office"]).exit_code == 0

    add_result = runner.invoke(app, ["add", str(remote_repo), "--skills", "word"])

    assert add_result.exit_code == 0, add_result.output
    manifest = load_lockfile(
        project_root / ".agents" / "environments" / "office.lock",
        expected_mode="project",
    )
    assert sorted(manifest.skills) == ["pdf", "word"]


def test_update_advances_active_environment_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist updated source revisions to the active environment manifest."""
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"pdf": skill_markdown("pdf", "PDF skill v1")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(project_root)
    assert runner.invoke(app, ["add", str(remote_repo), "--all"]).exit_code == 0
    assert runner.invoke(app, ["env", "create", "office"]).exit_code == 0
    assert runner.invoke(app, ["env", "activate", "office"]).exit_code == 0
    manifest_path = project_root / ".agents" / "environments" / "office.lock"
    previous_commit = load_lockfile(manifest_path, expected_mode="project").skills["pdf"].commit_hash
    write_skill(remote_repo / "pdf" / "SKILL.md", skill_markdown("pdf", "PDF skill v2"))
    git_commit(remote_repo, "Update PDF skill")

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    manifest = load_lockfile(manifest_path, expected_mode="project")
    assert manifest.skills["pdf"].commit_hash != previous_commit
    assert "PDF skill v2" in (project_root / ".agents" / "skills" / "pdf" / "SKILL.md").read_text(encoding="utf-8")


def make_project_root(path: Path) -> Path:
    """Create a minimal project root for CLI tests."""
    path.mkdir(parents=True)
    (path / ".git").mkdir()
    return path


def create_git_skill_repo(path: Path, skills: dict[str, str], container: str | None = None) -> Path:
    """Create and commit a temporary Git repository containing skills."""
    path.mkdir(parents=True)
    run(["git", "init", "-b", "main"], cwd=path)
    run(["git", "config", "user.name", "Test User"], cwd=path)
    run(["git", "config", "user.email", "test@example.com"], cwd=path)

    skills_root = path if container is None else path / container
    skills_root.mkdir(parents=True, exist_ok=True)
    for skill_name, content in skills.items():
        skill_dir = skills_root / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        write_skill(skill_dir / "SKILL.md", content)

    git_commit(path, "Initial skills")
    return path


def git_commit(repo_path: Path, message: str) -> None:
    """Create a Git commit in a test repository."""
    run(["git", "add", "."], cwd=repo_path)
    run(["git", "commit", "-m", message], cwd=repo_path)


def write_skill(path: Path, content: str) -> None:
    """Write a skill document to a test path."""
    path.write_text(content, encoding="utf-8")


def shutil_rmtree(path: Path) -> None:
    """Remove a directory tree using pathlib operations."""
    if path.exists():
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            else:
                child.rmdir()
        path.rmdir()


def run(command: list[str], cwd: Path) -> str:
    """Run a test subprocess and return stripped standard output."""
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=True)
    return completed.stdout.strip()


def skill_markdown(
    name: str,
    description: str,
    *,
    license: str | None = None,
    compatibility: str | None = None,
    allowed_tools: str | None = None,
    metadata: dict[str, str] | None = None,
    body: str | None = None,
) -> str:
    """Build skill markdown fixture content."""
    lines = ["---", f"name: {name}", "description: |", f"  {description}"]
    if license is not None:
        lines.append(f"license: {yaml_string(license)}")
    if compatibility is not None:
        lines.append(f"compatibility: {yaml_string(compatibility)}")
    if allowed_tools is not None:
        lines.append(f"allowed-tools: {yaml_string(allowed_tools)}")
    if metadata is not None:
        lines.append("metadata:")
        for key, value in metadata.items():
            lines.append(f"  {key}: {yaml_string(value)}")
    lines.append("---")
    lines.append("")
    lines.append(body or "## Instructions\n\nUse this skill carefully.\n")
    return "\n".join(lines)


def yaml_string(value: str) -> str:
    """Quote a string for YAML fixture content."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
