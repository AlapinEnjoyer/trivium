import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from trivium.cli import app
from trivium.lockfile import load_lockfile

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
    assert len(lockfile.skills["alpha-skill"].commit_hash) < 40
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
    assert load_lockfile(project_root / "skills.lock").skills == {}


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


def test_add_auth_failure_uses_exit_code_five(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = make_project_root(tmp_path / "project")
    monkeypatch.chdir(project_root)

    result = runner.invoke(app, ["add", "https://github.com/example/private.git", "--all"])

    assert result.exit_code == 5
    assert "Git Clone Failed" in result.output
    assert "credential.helper" in result.output


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
