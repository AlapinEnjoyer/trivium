import json
import shutil
import sys
from pathlib import Path

import questionary
import typer
from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.pretty import Pretty
from rich.rule import Rule
from rich.table import Table

from skill_trivium import __version__
from skill_trivium.context import ensure_storage, resolve_install_context
from skill_trivium.git import GitCloneError, cloned_repo
from skill_trivium.lockfile import load_lockfile, write_lockfile
from skill_trivium.models import InstallContext, ParsedSkill, SkillLockEntry, ValidationIssue
from skill_trivium.skills import (
    build_skill_markdown,
    discover_skills_path,
    enumerate_skill_directories,
    hash_parsed_skill,
    install_skill_tree,
    parse_skill_document,
    repair_installed_skill_if_needed,
    utc_now,
    validate_skill_directory,
    validate_skill_name,
)
from skill_trivium.ui import (
    console,
    make_panel,
    print_validation_issue,
    progress_bar,
    shorten_source,
    status_line,
    truncate_text,
)
from skill_trivium.update import render_update_summary, run_update

HELP_CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}
ADD_CONTEXT_SETTINGS = {**HELP_CONTEXT_SETTINGS, "allow_extra_args": True}

app = typer.Typer(
    add_completion=False,
    context_settings=HELP_CONTEXT_SETTINGS,
    no_args_is_help=True,
)


def version_callback(show_version: bool) -> None:
    if show_version:
        typer.echo(f"trivium {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version_flag: bool = typer.Option(
        None,
        "--version",
        "-V",
        help="Show the application's version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    pass


@app.command(context_settings=ADD_CONTEXT_SETTINGS, no_args_is_help=True)
def add(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="Git repository URL containing one or more skills."),
    all_: bool = typer.Option(False, "--all", "-a", help="Install all valid skills found at the resolved path."),
    skills: str | None = typer.Option(
        None,
        "--skills",
        "-s",
        metavar="NAME...",
        help="Install only the named skills that follow this flag, space-separated.",
    ),
    path: str | None = typer.Option(
        None,
        "--path",
        "-p",
        help="Path within the repository where skill directories are located (e.g. skills/, packages/ai-skills/). Auto-detected if omitted.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-resolve conflicts by replacing existing skills."),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview changes without writing files."),
    global_: bool = typer.Option(
        False,
        "--global",
        "-g",
        help="Install to ~/.agents/skills/ regardless of git context.",
    ),
) -> None:
    requested_names = _parse_add_skill_names(ctx, all_, skills)
    context = resolve_install_context(global_)
    lockfile = load_lockfile(context.lockfile_path)

    installed: list[str] = []
    would_install: list[str] = []
    skipped: dict[str, str] = {}
    failed: list[str] = []
    validation_issues: list[ValidationIssue] = []

    with progress_bar() as progress:
        task = progress.add_task("Cloning repository", total=None)
        try:
            with cloned_repo(url) as (repo_path, commit_hash):
                progress.update(task, completed=1)
                resolved = discover_skills_path(repo_path, path)
                if resolved is None:
                    title = "Skill Discovery Failed" if path is None else "Invalid Skills Path"
                    lines = (
                        [
                            "No skill directories containing SKILL.md were found in the repository root or repo/skills/.",
                            "Re-run the command with --path to point at the skills container directory explicitly.",
                        ]
                        if path is None
                        else [
                            f"The path '{path}' does not resolve to a skills container directory inside the repository.",
                            "Use --path to point at the directory whose children are individual skills.",
                        ]
                    )
                    console.print(make_panel("warn" if path is None else "err", title, lines))
                    raise typer.Exit(code=1 if path is None else 2)

                skills_container, skills_path = resolved
                candidates = enumerate_skill_directories(skills_container)
                candidate_map = {candidate.name: candidate for candidate in candidates}

                target_directories: list[Path]
                if requested_names is None:
                    target_directories = candidates
                else:
                    missing_names = [name for name in requested_names if name not in candidate_map]
                    if missing_names:
                        for name in missing_names:
                            issue = ValidationIssue(
                                skill_name=name,
                                field="name",
                                rule="The requested skill was not found in the remote repository.",
                            )
                            validation_issues.append(issue)
                            print_validation_issue(issue)
                            failed.append(name)
                    target_directories = [candidate_map[name] for name in requested_names if name in candidate_map]

                parsed_skills: list[ParsedSkill] = []
                for skill_dir in target_directories:
                    parsed_skill, issues = validate_skill_directory(skill_dir)
                    if issues:
                        validation_issues.extend(issues)
                        for issue in issues:
                            print_validation_issue(issue)
                        failed.append(skill_dir.name)
                        continue
                    parsed_skills.append(parsed_skill)

                pending_installs: list[ParsedSkill] = []
                conflicts: list[tuple[ParsedSkill, SkillLockEntry]] = []
                repaired: list[str] = []
                for parsed_skill in parsed_skills:
                    existing = lockfile.skills.get(parsed_skill.name)
                    if existing is None:
                        pending_installs.append(parsed_skill)
                        continue
                    if existing.source_url == url:
                        if not dry_run and repair_installed_skill_if_needed(
                            parsed_skill, context.install_path_for(parsed_skill.name)
                        ):
                            repaired.append(parsed_skill.name)
                            for warning in parsed_skill.warnings:
                                console.print(make_panel("warn", f"Conversion Warning: {parsed_skill.name}", [warning]))
                        skipped[parsed_skill.name] = "already installed from the same source"
                        continue
                    conflicts.append((parsed_skill, existing))

                # Stop the live display before printing conflict panels or
                # prompting so Rich and Questionary do not compete for the terminal.
                progress.stop()

                if conflicts and not yes and not _is_interactive_terminal():
                    for parsed_skill, existing in conflicts:
                        console.print(_conflict_panel(parsed_skill, existing, url, commit_hash))
                    raise typer.Exit(code=4)

                replaced: list[str] = []
                if yes:
                    for parsed_skill, _existing in conflicts:
                        pending_installs.append(parsed_skill)
                        replaced.append(parsed_skill.name)
                        skipped.pop(parsed_skill.name, None)
                else:
                    for parsed_skill, existing in conflicts:
                        console.print(_conflict_panel(parsed_skill, existing, url, commit_hash))
                        choice = questionary.select(
                            f"Resolve conflict for '{parsed_skill.name}'",
                            choices=["Keep existing", "Replace with new", "Skip"],
                        ).ask()
                        if choice == "Replace with new":
                            pending_installs.append(parsed_skill)
                            replaced.append(parsed_skill.name)
                        elif choice == "Skip":
                            skipped[parsed_skill.name] = "skipped"
                        else:
                            skipped[parsed_skill.name] = "kept existing"

                for parsed_skill in pending_installs:
                    installed_at = utc_now()
                    entry = _entry_from_skill(
                        parsed_skill=parsed_skill,
                        source_url=url,
                        commit_hash=commit_hash,
                        skills_path=skills_path,
                        context=context,
                        installed_at=installed_at,
                    )
                    if dry_run:
                        would_install.append(parsed_skill.name)
                        continue
                    ensure_storage(context)
                    destination = context.install_path_for(parsed_skill.name)
                    install_skill_tree(parsed_skill, destination)
                    for warning in parsed_skill.warnings:
                        console.print(make_panel("warn", f"Conversion Warning: {parsed_skill.name}", [warning]))
                    lockfile.skills[parsed_skill.name] = entry
                    installed.append(parsed_skill.name)

                if pending_installs and not dry_run:
                    write_lockfile(context, lockfile)

                if replaced and not dry_run:
                    console.print(
                        make_panel(
                            "info",
                            "Conflicts Replaced",
                            [f"Replaced skill '{name}' with the incoming source." for name in sorted(replaced)],
                        )
                    )
                if repaired:
                    console.print(
                        make_panel(
                            "info",
                            "Normalized Installed Skills",
                            [
                                f"Rewrote installed SKILL.md for '{name}' to match normalized metadata."
                                for name in repaired
                            ],
                        )
                    )
        except GitCloneError as error:
            lines = [error.stderr]
            if error.guidance is not None:
                lines.append(error.guidance)
            console.print(make_panel("warn" if error.auth_failure else "err", "Git Clone Failed", lines))
            raise typer.Exit(code=5 if error.auth_failure else 1) from error

    summary_lines = _summary_lines(installed, would_install, skipped, failed)
    if summary_lines:
        title = "Dry Run" if dry_run else "Add Summary"
        console.print(make_panel("ok" if not failed else "info", title, summary_lines))

    if validation_issues:
        raise typer.Exit(code=2)
    if dry_run and would_install:
        raise typer.Exit(code=3)


@app.command()
def update(
    skills: list[str] | None = typer.Argument(None, help="Optional installed skill names to update."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Preview changes without writing files.",
    ),
    global_: bool = typer.Option(
        False,
        "--global",
        "-g",
        help="Update skills in the global install instead of the current project.",
    ),
) -> None:
    context = resolve_install_context(global_)
    lockfile = load_lockfile(context.lockfile_path)
    if not lockfile.skills:
        console.print(make_panel("info", "No Skills Installed", ["Nothing to update."]))
        raise typer.Exit()

    requested_skills = skills or []
    missing = [name for name in requested_skills if name not in lockfile.skills]
    if missing:
        for name in missing:
            print_validation_issue(
                ValidationIssue(skill_name=name, field="name", rule="The requested skill is not installed.")
            )
        raise typer.Exit(code=2)

    outcome = run_update(lockfile=lockfile, context=context, requested_skills=requested_skills, dry_run=dry_run)
    render_update_summary(outcome, dry_run=dry_run)

    exit_code = outcome.exit_code(dry_run=dry_run)
    if exit_code is not None:
        raise typer.Exit(code=exit_code)


@app.command("list")
def list_skills(
    json_: bool = typer.Option(False, "--json", "-j", help="Print the full skills.lock contents as JSON."),
    global_: bool = typer.Option(
        False,
        "--global",
        "-g",
        help="List skills from the global install instead of the current project.",
    ),
) -> None:
    _render_skill_list(json_=json_, global_=global_)


@app.command(no_args_is_help=True)
def info(
    skill_name: str = typer.Argument(..., help="Installed skill name to inspect."),
    global_: bool = typer.Option(False, "--global", "-g", help="Look up the skill in the global install."),
) -> None:
    context = resolve_install_context(global_)
    lockfile = load_lockfile(context.lockfile_path)
    entry = lockfile.skills.get(skill_name)
    if entry is None:
        print_validation_issue(
            ValidationIssue(skill_name=skill_name, field="name", rule="The requested skill is not installed.")
        )
        raise typer.Exit(code=2)

    markdown_body = "_Installed SKILL.md body could not be read._"
    skill_file = context.base_dir / entry.install_path / "SKILL.md"
    if skill_file.is_file():
        try:
            _, body = parse_skill_document(skill_file)
            markdown_body = body or "_No markdown body content._"
        except ValueError:
            markdown_body = "_Installed SKILL.md body could not be parsed._"

    details: list[object] = [
        status_line("info", f"Source URL: {entry.source_url}"),
        status_line("info", f"Commit Hash: {entry.commit_hash}"),
        status_line("info", f"Skills Path: {entry.skills_path}"),
        status_line("info", f"Install Path: {entry.install_path}"),
        status_line("info", f"Installed At: {entry.installed_at}"),
        status_line("info", f"Description: {entry.description}"),
    ]
    if entry.license is not None:
        details.append(status_line("info", f"License: {entry.license}"))
    if entry.compatibility is not None:
        details.append(status_line("info", f"Compatibility: {entry.compatibility}"))
    if entry.allowed_tools is not None:
        details.append(status_line("info", f"Allowed Tools: {entry.allowed_tools}"))
    if entry.metadata is not None:
        details.extend([Rule("metadata"), Pretty(entry.metadata)])

    details.extend([Rule("SKILL.md"), Markdown(markdown_body)])
    console.print(Panel(Group(*details), title=skill_name, border_style="blue"))


@app.command(no_args_is_help=True)
def remove(
    skills: list[str] | None = typer.Argument(None, help="Installed skill names to remove."),
    all_: bool = typer.Option(False, "--all", "-a", help="Remove all installed skills."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Bypass confirmation prompts."),
    global_: bool = typer.Option(
        False,
        "--global",
        "-g",
        help="Remove skills from the global install instead of the current project.",
    ),
) -> None:
    requested_skills = skills or []
    if all_ and requested_skills:
        console.print(make_panel("err", "Invalid Arguments", ["Use either skill names or --all, not both."]))
        raise typer.Exit(code=2)
    if not all_ and not requested_skills:
        console.print(make_panel("err", "Invalid Arguments", ["Provide one or more skill names, or use --all."]))
        raise typer.Exit(code=2)

    context = resolve_install_context(global_)
    lockfile = load_lockfile(context.lockfile_path)
    if not lockfile.skills:
        console.print(make_panel("info", "No Skills Installed", ["Nothing to remove."]))
        raise typer.Exit()

    target_names = sorted(lockfile.skills) if all_ else list(dict.fromkeys(requested_skills))
    missing = [name for name in target_names if name not in lockfile.skills]
    if missing:
        for name in missing:
            print_validation_issue(
                ValidationIssue(skill_name=name, field="name", rule="The requested skill is not installed.")
            )
        raise typer.Exit(code=2)

    if not yes:
        prompt = f"Remove {len(target_names)} skill{'s' if len(target_names) != 1 else ''}?"
        if not _confirm(prompt):
            console.print(make_panel("info", "Remove Cancelled", ["No skills were removed."]))
            raise typer.Exit()

    ensure_storage(context)
    for name in target_names:
        skill_dir = context.install_path_for(name)
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
        lockfile.skills.pop(name, None)

    write_lockfile(context, lockfile)
    console.print(
        make_panel(
            "ok",
            "Remove Summary",
            [f"Removed skill '{name}'." for name in target_names],
        )
    )


@app.command(no_args_is_help=True)
def init(
    skill_name: str = typer.Argument(..., help="Skill directory name to scaffold."),
    full: bool = typer.Option(
        False, "--full", "-f", help="Also create scripts/, references/, and assets/ directories."
    ),
    global_: bool = typer.Option(
        False,
        "--global",
        "-g",
        help="Create the scaffold in ~/.agents/skills/ regardless of git context.",
    ),
) -> None:
    validation_issue = _validate_init_name(skill_name)
    if validation_issue is not None:
        print_validation_issue(validation_issue)
        raise typer.Exit(code=2)

    context = resolve_install_context(global_)
    destination = context.install_path_for(skill_name)
    if destination.exists():
        console.print(
            make_panel(
                "err",
                "Skill Already Exists",
                [f"The destination '{destination}' already exists."],
            )
        )
        raise typer.Exit(code=1)

    ensure_storage(context)
    lockfile = load_lockfile(context.lockfile_path)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "SKILL.md").write_text(build_skill_markdown(skill_name), encoding="utf-8")
    if full:
        for directory_name in ("scripts", "references", "assets"):
            (destination / directory_name).mkdir()

    write_lockfile(context, lockfile)

    console.print(
        make_panel(
            "ok",
            "Skill Initialized",
            [f"Created skill scaffold at '{destination}'."],
        )
    )


def main() -> None:
    app()


def _parse_add_skill_names(ctx: typer.Context, all_: bool, skills_value: str | None) -> list[str] | None:
    extra_args = list(dict.fromkeys(ctx.args))
    if all_ and skills_value is not None:
        console.print(make_panel("err", "Invalid Arguments", ["Use either --all or --skills, not both."]))
        raise typer.Exit(code=2)
    if not all_ and skills_value is None:
        console.print(make_panel("err", "Invalid Arguments", ["Use either --all or --skills."]))
        raise typer.Exit(code=2)
    if all_ and extra_args:
        console.print(make_panel("err", "Invalid Arguments", ["Skill names may only follow the --skills flag."]))
        raise typer.Exit(code=2)
    if all_:
        return None

    requested_names = [skills_value, *extra_args] if skills_value is not None else []
    if not requested_names:
        console.print(make_panel("err", "Invalid Arguments", ["Provide one or more skill names after --skills."]))
        raise typer.Exit(code=2)

    return list(dict.fromkeys(requested_names))


def _entry_from_skill(
    *,
    parsed_skill: ParsedSkill,
    source_url: str,
    commit_hash: str,
    skills_path: str,
    context: InstallContext,
    installed_at: str,
) -> SkillLockEntry:
    return SkillLockEntry(
        name=parsed_skill.name,
        source_url=source_url,
        commit_hash=commit_hash,
        content_hash=hash_parsed_skill(parsed_skill),
        skills_path=skills_path,
        install_path=context.relative_install_path(parsed_skill.name),
        description=parsed_skill.description,
        license=parsed_skill.license,
        compatibility=parsed_skill.compatibility,
        allowed_tools=parsed_skill.allowed_tools,
        installed_at=installed_at,
        metadata=parsed_skill.metadata,
    )


def _summary_lines(
    installed: list[str],
    would_install: list[str],
    skipped: dict[str, str],
    failed: list[str],
) -> list[str]:
    lines: list[str] = []
    lines.extend(f"Installed: {name}" for name in sorted(installed))
    lines.extend(f"Would install: {name}" for name in sorted(would_install))
    lines.extend(f"Skipped: {name} ({reason})" for name, reason in sorted(skipped.items()))
    lines.extend(f"Failed: {name}" for name in sorted(set(failed)))
    return lines


def _conflict_panel(
    parsed_skill: ParsedSkill,
    existing: SkillLockEntry,
    incoming_source_url: str,
    incoming_commit_hash: str,
) -> Panel:
    lines = [
        f"Skill '{parsed_skill.name}' already exists from a different source.",
        f"Existing: {existing.source_url} @ {existing.commit_hash} (installed {existing.installed_at})",
        f"Incoming: {incoming_source_url} @ {incoming_commit_hash}",
    ]
    return make_panel("warn", f"Conflict: {parsed_skill.name}", lines)


def _render_skill_list(*, json_: bool, global_: bool) -> None:
    context = resolve_install_context(global_)
    lockfile = load_lockfile(context.lockfile_path)
    if json_:
        console.out(json.dumps(lockfile.to_dict(), indent=2, sort_keys=True))
        return

    table = Table(title="Installed Skills")
    table.add_column("Name", style="bold")
    table.add_column("Description")
    table.add_column("Compatibility")
    table.add_column("Source")
    table.add_column("Commit")

    for name in sorted(lockfile.skills):
        entry = lockfile.skills[name]
        table.add_row(
            entry.name,
            truncate_text(entry.description, width=60),
            truncate_text(entry.compatibility or "", width=30),
            shorten_source(entry.source_url, width=36),
            entry.commit_hash[:7],
        )

    if not lockfile.skills:
        table.caption = "No installed skills"
    console.print(table)


def _is_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _confirm(prompt: str) -> bool:
    if _is_interactive_terminal():
        return bool(questionary.confirm(prompt, default=False).ask())

    response = sys.stdin.readline()
    if not response:
        console.print(make_panel("warn", "No Input", ["stdin reached EOF; treating as 'no'."]))
        return False
    return response.strip().lower() in {"y", "yes"}


def _validate_init_name(skill_name: str) -> ValidationIssue | None:
    issues = validate_skill_name(skill_name, skill_name=skill_name)
    if not issues:
        return None
    return issues[0]
