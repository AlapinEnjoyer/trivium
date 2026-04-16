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
    result = runner.invoke(app, [])

    assert result.exit_code == 2, result.output
    assert "Usage:" in result.output
    assert "Commands" in result.output


def test_short_help_flag_works_on_root_and_subcommand() -> None:
    root_result = runner.invoke(app, ["-h"])
    init_result = runner.invoke(app, ["init", "-h"])

    assert root_result.exit_code == 0, root_result.output
    assert init_result.exit_code == 0, init_result.output
    assert "Usage:" in root_result.output
    assert "Usage:" in init_result.output


def test_version_flag_shows_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("trivium ")


def test_init_without_required_argument_shows_help() -> None:
    result = runner.invoke(app, ["init"])

    assert result.exit_code == 2, result.output
    assert "Usage:" in result.output
    assert "SKILL_NAME" in result.output


def test_init_scaffolds_skill_in_project_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["init", "demo-skill", "--full"])

    assert result.exit_code == 0, result.output
    skill_dir = project_root / ".agents" / "skills" / "demo-skill"
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "scripts").is_dir()
    assert (skill_dir / "references").is_dir()
    assert (skill_dir / "assets").is_dir()
    assert (project_root / "skills.lock").is_file()


def test_init_rejects_invalid_skill_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".git").mkdir()
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["init", "Bad-Name"])

    assert result.exit_code == 2
    assert "Validation Failed" in result.output
    assert "single hyphens" in result.output


def test_add_installs_all_valid_skills_and_writes_lockfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_add_normalizes_yaml_list_allowed_tools_in_installed_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_add_same_source_readd_repairs_installed_skill_frontmatter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    write_skill(
        installed_path,
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
                "Use this skill carefully.",
            ]
        ),
    )

    second = runner.invoke(app, ["add", str(remote_repo), "--all"])

    assert second.exit_code == 0, second.output
    frontmatter, _ = parse_skill_document(installed_path)
    assert frontmatter["allowed-tools"] == "Bash Read"
    assert not isinstance(frontmatter["allowed-tools"], list)


def test_list_shows_installed_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["list"])

    assert result.exit_code == 0, result.output
    assert "No installed skills" in result.output
    assert not (project_root / "skills.lock").exists()


def test_ls_is_not_supported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["ls"])

    assert result.exit_code == 2
    assert "No such command 'ls'" in result.output


def test_list_json_outputs_lockfile_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_remove_reads_yes_from_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"alpha-skill": skill_markdown("alpha-skill", "Alpha")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)
    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output

    result = runner.invoke(app, ["remove", "--all"], input="y\n")

    assert result.exit_code == 0, result.output
    assert not (project_root / ".agents" / "skills" / "alpha-skill").exists()


def test_remove_eof_from_stdin_warns_and_cancels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"alpha-skill": skill_markdown("alpha-skill", "Alpha")},
    )
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)
    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output

    result = runner.invoke(app, ["remove", "--all"], input="")

    assert result.exit_code == 0, result.output
    assert "No Input" in result.output
    assert "Remove Cancelled" in result.output
    assert (project_root / ".agents" / "skills" / "alpha-skill").exists()


def test_update_refreshes_skill_from_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert "No Skills Installed" in result.output
    assert not (project_root / "skills.lock").exists()


def test_update_only_reinstalls_changed_skill_from_shared_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_update_without_content_hash_only_refreshes_lockfile_for_unchanged_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0, result.output
    assert "Up To Date" in result.output
    assert "Updated skill 'alpha-skill'." not in result.output
    assert installed_path.read_text(encoding="utf-8") == before
    assert "content_hash" in lockfile_path.read_text(encoding="utf-8")


def test_update_warns_when_skill_path_or_skill_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert "re-run `trivium add".lower() in result.output.lower()


def test_add_same_source_readd_repairs_installed_skill_metadata_scalar_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert "Conversion Warning: capture-api-response-test-fixture" in result.output
    frontmatter, _ = parse_skill_document(installed_path)
    assert frontmatter["metadata"] == {"author": "example-org", "internal": "true"}


def test_global_mode_uses_home_agents_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_local_mode_outside_git_repo_uses_current_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["add", "https://github.com/example/private.git", "--all"])

    assert result.exit_code == 5
    assert "Git Clone Failed" in result.output
    assert "credential.helper" in result.output


def test_env_create_captures_current_runtime_and_list_shows_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "pptx": skill_markdown("pptx", "PowerPoint skill"),
            "pdf": skill_markdown("pdf", "PDF skill"),
        },
    )
    project_root = make_project_root(tmp_path / "project")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(project_root)

    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output

    create_result = runner.invoke(app, ["env", "create", "office"])

    assert create_result.exit_code == 0, create_result.output
    assert "Environment Created" in create_result.output
    assert (fake_home / ".trivium" / "projects").is_dir()

    env_list = runner.invoke(app, ["env", "list"])

    assert env_list.exit_code == 0, env_list.output
    assert "office" in env_list.output
    assert "2" in env_list.output


def test_env_create_global_captures_current_project_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "pptx": skill_markdown("pptx", "PowerPoint skill"),
            "pdf": skill_markdown("pdf", "PDF skill"),
        },
    )
    project_root = make_project_root(tmp_path / "project")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(project_root)

    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output

    create_result = runner.invoke(app, ["env", "create", "office", "--global"])

    assert create_result.exit_code == 0, create_result.output
    assert "Captured 2 skills." in create_result.output
    global_env_lockfile = fake_home / ".trivium" / "global" / "envs" / "office" / "skills.lock"
    assert global_env_lockfile.is_file()
    assert sorted(load_lockfile(global_env_lockfile).skills) == ["pdf", "pptx"]


def test_project_can_activate_globally_stored_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "pptx": skill_markdown("pptx", "PowerPoint skill"),
            "pdf": skill_markdown("pdf", "PDF skill"),
        },
    )
    source_project = make_project_root(tmp_path / "source-project")
    target_project = make_project_root(tmp_path / "target-project")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    monkeypatch.chdir(source_project)
    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output
    create_result = runner.invoke(app, ["env", "create", "office", "--global"])
    assert create_result.exit_code == 0, create_result.output

    monkeypatch.chdir(target_project)
    activate_result = runner.invoke(app, ["env", "activate", "office"])

    assert activate_result.exit_code == 0, activate_result.output
    assert (target_project / ".agents" / "skills" / "pdf" / "SKILL.md").is_file()
    assert (target_project / ".agents" / "skills" / "pptx" / "SKILL.md").is_file()
    assert load_lockfile(target_project / "skills.lock").meta["environment"] == "office"


def test_env_list_global_shows_globally_stored_environments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"pdf": skill_markdown("pdf", "PDF skill")},
    )
    project_root = make_project_root(tmp_path / "project")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(project_root)

    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output
    create_result = runner.invoke(app, ["env", "create", "office", "--global"])
    assert create_result.exit_code == 0, create_result.output

    list_result = runner.invoke(app, ["env", "list", "--global"])

    assert list_result.exit_code == 0, list_result.output
    assert "office" in list_result.output


def test_env_activate_and_deactivate_swaps_runtime_skill_sets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "pdf": skill_markdown("pdf", "PDF skill"),
            "word": skill_markdown("word", "Word skill"),
        },
    )
    project_root = make_project_root(tmp_path / "project")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(project_root)

    runner.invoke(app, ["add", str(remote_repo), "--skills", "pdf"])
    office_result = runner.invoke(app, ["env", "create", "office"])
    assert office_result.exit_code == 0, office_result.output

    runner.invoke(app, ["remove", "pdf", "--yes"])
    runner.invoke(app, ["add", str(remote_repo), "--skills", "word"])
    creativity_result = runner.invoke(app, ["env", "create", "creativity"])
    assert creativity_result.exit_code == 0, creativity_result.output

    activate_office = runner.invoke(app, ["env", "activate", "office"])
    assert activate_office.exit_code == 0, activate_office.output
    assert (project_root / ".agents" / "skills" / "pdf").is_dir()
    assert not (project_root / ".agents" / "skills" / "word").exists()
    assert load_lockfile(project_root / "skills.lock").meta["environment"] == "office"

    activate_creativity = runner.invoke(app, ["env", "activate", "creativity"])
    assert activate_creativity.exit_code == 0, activate_creativity.output
    assert (project_root / ".agents" / "skills" / "word").is_dir()
    assert not (project_root / ".agents" / "skills" / "pdf").exists()
    assert load_lockfile(project_root / "skills.lock").meta["environment"] == "creativity"

    deactivate_result = runner.invoke(app, ["env", "deactivate"])
    assert deactivate_result.exit_code == 0, deactivate_result.output
    assert (project_root / ".agents" / "skills" / "word").is_dir()
    assert not (project_root / ".agents" / "skills" / "pdf").exists()
    assert (
        not (project_root / "skills.lock").exists()
        or "environment" not in load_lockfile(project_root / "skills.lock").meta
    )


def test_env_activate_shared_materializes_local_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"pdf": skill_markdown("pdf", "PDF skill")},
    )
    project_root = make_project_root(tmp_path / "project")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(project_root)

    runner.invoke(app, ["add", str(remote_repo), "--all"])
    create_result = runner.invoke(app, ["env", "create", "office", "--shared"])
    assert create_result.exit_code == 0, create_result.output

    local_env_dir = next((fake_home / ".trivium" / "projects").glob("*/envs/office"))
    shutil_rmtree(local_env_dir)

    runner.invoke(app, ["remove", "pdf", "--yes"])
    activate_result = runner.invoke(app, ["env", "activate", "office"])

    assert activate_result.exit_code == 0, activate_result.output
    assert (project_root / ".agents" / "skills" / "pdf").is_dir()
    assert next((fake_home / ".trivium" / "projects").glob("*/envs/office/skills/pdf/SKILL.md")).is_file()


def test_add_updates_active_environment_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "pdf": skill_markdown("pdf", "PDF skill"),
            "word": skill_markdown("word", "Word skill"),
        },
    )
    project_root = make_project_root(tmp_path / "project")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(project_root)

    runner.invoke(app, ["add", str(remote_repo), "--skills", "pdf"])
    runner.invoke(app, ["env", "create", "office"])
    runner.invoke(app, ["env", "activate", "office"])

    add_result = runner.invoke(app, ["add", str(remote_repo), "--skills", "word"])

    assert add_result.exit_code == 0, add_result.output

    runner.invoke(app, ["env", "deactivate"])
    runner.invoke(app, ["remove", "pdf", "word", "--yes"])
    reactivate_result = runner.invoke(app, ["env", "activate", "office"])

    assert reactivate_result.exit_code == 0, reactivate_result.output
    assert (project_root / ".agents" / "skills" / "pdf").is_dir()
    assert (project_root / ".agents" / "skills" / "word").is_dir()


def test_env_create_fails_when_runtime_contains_unmanaged_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = make_project_root(tmp_path / "project")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(project_root)

    unmanaged_dir = project_root / ".agents" / "skills" / "local-only"
    unmanaged_dir.mkdir(parents=True)
    write_skill(unmanaged_dir / "SKILL.md", skill_markdown("local-only", "Local only skill"))

    result = runner.invoke(app, ["env", "create", "office"])

    assert result.exit_code == 2
    assert "Unmanaged Skills Detected" in result.output


def test_init_is_blocked_while_environment_is_active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"pdf": skill_markdown("pdf", "PDF skill")},
    )
    project_root = make_project_root(tmp_path / "project")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(project_root)

    runner.invoke(app, ["add", str(remote_repo), "--all"])
    runner.invoke(app, ["env", "create", "office"])
    runner.invoke(app, ["env", "activate", "office"])

    result = runner.invoke(app, ["init", "draft-skill"])

    assert result.exit_code == 2
    assert "Init Blocked While Environment Is Active" in result.output


def test_env_remove_deletes_local_and_shared_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"pdf": skill_markdown("pdf", "PDF skill")},
    )
    project_root = make_project_root(tmp_path / "project")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(project_root)

    runner.invoke(app, ["add", str(remote_repo), "--all"])
    create_result = runner.invoke(app, ["env", "create", "office", "--shared"])
    assert create_result.exit_code == 0, create_result.output

    remove_result = runner.invoke(app, ["env", "remove", "office"])

    assert remove_result.exit_code == 0, remove_result.output
    assert "Environment Removed" in remove_result.output
    assert "Removed the local snapshot." in remove_result.output
    assert "Removed the shared environment definition." in remove_result.output
    assert not list((fake_home / ".trivium" / "projects").glob("*/envs/office"))
    assert not (project_root / ".agents" / "environments" / "office.lock").exists()


def test_env_remove_auto_deactivates_active_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {
            "pdf": skill_markdown("pdf", "PDF skill"),
            "word": skill_markdown("word", "Word skill"),
        },
    )
    project_root = make_project_root(tmp_path / "project")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(project_root)

    runner.invoke(app, ["add", str(remote_repo), "--skills", "pdf"])
    runner.invoke(app, ["env", "create", "office"])
    runner.invoke(app, ["remove", "pdf", "--yes"])
    runner.invoke(app, ["add", str(remote_repo), "--skills", "word"])
    runner.invoke(app, ["env", "activate", "office"])

    remove_result = runner.invoke(app, ["env", "remove", "office"])

    assert remove_result.exit_code == 0, remove_result.output
    assert "Deactivated it and restored the default runtime first." in remove_result.output
    assert (project_root / ".agents" / "skills" / "word").is_dir()
    assert not (project_root / ".agents" / "skills" / "pdf").exists()


def test_env_activate_can_materialize_from_shared_lockfile_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote_repo = create_git_skill_repo(
        tmp_path / "remote",
        {"pdf": skill_markdown("pdf", "PDF skill")},
    )
    project_root = make_project_root(tmp_path / "project")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(project_root)

    runner.invoke(app, ["add", str(remote_repo), "--all"])
    runner.invoke(app, ["env", "create", "office", "--shared"])
    runner.invoke(app, ["env", "remove", "office"])

    shared_lockfile = project_root / ".agents" / "environments" / "office.lock"
    shared_lockfile.parent.mkdir(parents=True, exist_ok=True)
    shared_lockfile.write_text((project_root / "skills.lock").read_text(encoding="utf-8"), encoding="utf-8")
    runner.invoke(app, ["remove", "pdf", "--yes"])

    activate_result = runner.invoke(app, ["env", "activate", "office"])

    assert activate_result.exit_code == 0, activate_result.output
    assert (project_root / ".agents" / "skills" / "pdf" / "SKILL.md").is_file()


def test_env_remove_active_shared_materialized_environment_restores_default_without_dirty_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(project_root)

    add_result = runner.invoke(app, ["add", str(remote_repo), "--all"])
    assert add_result.exit_code == 0, add_result.output
    create_result = runner.invoke(app, ["env", "create", "caveman", "--shared"])
    assert create_result.exit_code == 0, create_result.output
    remove_result = runner.invoke(app, ["env", "remove", "caveman"])
    assert remove_result.exit_code == 0, remove_result.output

    shared_lockfile = project_root / ".agents" / "environments" / "caveman.lock"
    shared_lockfile.parent.mkdir(parents=True, exist_ok=True)
    shared_lockfile.write_text((project_root / "skills.lock").read_text(encoding="utf-8"), encoding="utf-8")

    activate_result = runner.invoke(app, ["env", "activate", "caveman"])
    assert activate_result.exit_code == 0, activate_result.output

    active_remove_result = runner.invoke(app, ["env", "remove", "caveman"])

    assert active_remove_result.exit_code == 0, active_remove_result.output
    assert "Runtime Has Local Modifications" not in active_remove_result.output

    missing_activate_result = runner.invoke(app, ["env", "activate", "caveman"])

    assert missing_activate_result.exit_code == 2
    assert "Environment Not Found" in missing_activate_result.output


def make_project_root(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / ".git").mkdir()
    return path


def create_git_skill_repo(path: Path, skills: dict[str, str], container: str | None = None) -> Path:
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
    run(["git", "add", "."], cwd=repo_path)
    run(["git", "commit", "-m", message], cwd=repo_path)


def write_skill(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def shutil_rmtree(path: Path) -> None:
    if path.exists():
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            else:
                child.rmdir()
        path.rmdir()


def run(command: list[str], cwd: Path) -> str:
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
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
