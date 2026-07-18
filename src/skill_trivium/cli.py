"""Define the public Typer commands for managing installed agent skills.

The command handlers translate CLI options into the lower-level context,
repository, lockfile, skill, update, and environment services, while keeping
terminal rendering and exit-code decisions at the application boundary.
"""

import json
import sys

import questionary
import typer
from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.pretty import Pretty
from rich.rule import Rule
from rich.table import Table

from skill_trivium import __version__
from skill_trivium.add import run_add
from skill_trivium.context import resolve_install_context
from skill_trivium.environment import (
    EnvironmentError,
    activate_environment,
    active_environment_name,
    create_environment,
    deactivate_environment,
    describe_environment,
    list_environments,
    remove_environment,
)
from skill_trivium.lockfile import load_lockfile
from skill_trivium.models import ValidationIssue
from skill_trivium.remove import run_remove
from skill_trivium.skills import (
    parse_skill_document,
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
env_app = typer.Typer(context_settings=HELP_CONTEXT_SETTINGS, no_args_is_help=True)
app.add_typer(env_app, name="env")


def version_callback(show_version: bool) -> None:
    """Print the package version and exit when the version flag is set."""
    if show_version:
        typer.echo(f"trivium {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    _version_flag: bool = typer.Option(
        None,
        "--version",
        "-V",
        help="Show the application's version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """Configure the root command and its eager version option."""
    pass


@app.command(context_settings=ADD_CONTEXT_SETTINGS, no_args_is_help=True)
def add(
    ctx: typer.Context,
    url: str = typer.Argument(..., help="Git repository URL containing one or more skills."),
    all_: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Install all valid skills found at the resolved path. (default if neither --all nor --skills is specified)",
    ),
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
    ignore_validation: bool = typer.Option(
        False,
        "--ignore-validation",
        "-i",
        help="Ignore skill validation errors and install skills even if they have issues. Must be explicitly set.",
    ),
    global_: bool = typer.Option(
        False,
        "--global",
        "-g",
        help="Install to ~/.agents/skills/ regardless of git context.",
    ),
) -> None:
    """Install selected skills from a Git repository.

    Args:
        ctx: Typer command context used by the add workflow.
        url: Repository URL containing one or more skill directories.
        all_: Install every valid skill found in the repository.
        skills: Optional space-separated names to install.
        path: Optional repository-relative skills container path.
        yes: Replace conflicts without prompting.
        dry_run: Preview changes without writing files.
        ignore_validation: Install skills despite validation issues.
        global_: Install into the global agent directory.
    """
    run_add(
        ctx=ctx,
        url=url,
        all_=all_,
        skills=skills,
        path=path,
        yes=yes,
        dry_run=dry_run,
        ignore_validation=ignore_validation,
        global_=global_,
        progress_factory=progress_bar,  # ty:ignore[invalid-argument-type]
        is_interactive_terminal=_is_interactive_terminal,
        select_conflict=_select_add_conflict,
    )


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
    """Update installed skills from their recorded source repositories.

    Args:
        skills: Optional installed skill names. An omitted value updates all.
        dry_run: Preview updates without changing the runtime or lockfile.
        global_: Update the global installation instead of the current project.
    """
    context = resolve_install_context(global_)
    try:
        outcome = run_update(
            context=context,
            requested_skills=skills or [],
            dry_run=dry_run,
        )
    except EnvironmentError as error:
        _print_environment_error(error)
        raise typer.Exit(code=error.exit_code) from error
    except OSError as error:
        console.print(make_panel("err", "Update Failed", [str(error)]))
        raise typer.Exit(code=1) from error

    if outcome.nothing_installed:
        if skills:
            for name in dict.fromkeys(skills):
                print_validation_issue(
                    ValidationIssue(skill_name=name, field="name", rule="The requested skill is not installed.")
                )
            raise typer.Exit(code=2)
        console.print(make_panel("info", "No Skills Installed", ["Nothing to update."]))
        return

    for name in outcome.missing_names:
        print_validation_issue(
            ValidationIssue(skill_name=name, field="name", rule="The requested skill is not installed.")
        )

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
    """List installed skills in human-readable or JSON form."""
    _render_skill_list(json_=json_, global_=global_)


@app.command(no_args_is_help=True)
def info(
    skill_name: str = typer.Argument(..., help="Installed skill name to inspect."),
    global_: bool = typer.Option(False, "--global", "-g", help="Look up the skill in the global install."),
) -> None:
    """Show lockfile metadata and the installed skill document."""
    context = resolve_install_context(global_)
    lockfile = load_lockfile(context.lockfile_path, expected_mode=context.mode)
    entry = lockfile.skills.get(skill_name)
    if entry is None:
        print_validation_issue(
            ValidationIssue(skill_name=skill_name, field="name", rule="The requested skill is not installed.")
        )
        raise typer.Exit(code=2)

    markdown_body = "_Installed SKILL.md body could not be read._"
    skill_dir = context.install_path_for(entry.name)
    skill_file = skill_dir / "SKILL.md"
    if not skill_dir.is_symlink() and skill_file.is_file():
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
    console.print(Panel(Group(*details), title=skill_name, border_style="blue"))  # ty:ignore[invalid-argument-type]


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
    """Remove selected or all installed skills."""
    requested_skills = skills or []
    if all_ and requested_skills:
        console.print(make_panel("err", "Invalid Arguments", ["Use either skill names or --all, not both."]))
        raise typer.Exit(code=2)
    if not all_ and not requested_skills:
        console.print(make_panel("err", "Invalid Arguments", ["Provide one or more skill names, or use --all."]))
        raise typer.Exit(code=2)

    context = resolve_install_context(global_)
    lockfile = load_lockfile(context.lockfile_path, expected_mode=context.mode)
    if not lockfile.skills:
        if requested_skills:
            for name in dict.fromkeys(requested_skills):
                print_validation_issue(
                    ValidationIssue(skill_name=name, field="name", rule="The requested skill is not installed.")
                )
            raise typer.Exit(code=2)
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
        if not _is_interactive_terminal():
            console.print(make_panel("err", "Confirmation Required", ["Non-interactive removal requires --yes."]))
            raise typer.Exit(code=4)
        prompt = f"Remove {len(target_names)} skill{'s' if len(target_names) != 1 else ''}?"
        if not _confirm(prompt):
            console.print(make_panel("info", "Remove Cancelled", ["No skills were removed."]))
            raise typer.Exit()

    try:
        outcome = run_remove(context, target_names)
    except EnvironmentError as error:
        _print_environment_error(error)
        raise typer.Exit(code=error.exit_code) from error
    except OSError as error:
        console.print(make_panel("err", "Remove Failed", [str(error)]))
        raise typer.Exit(code=1) from error

    if outcome.missing:
        for name in outcome.missing:
            print_validation_issue(
                ValidationIssue(skill_name=name, field="name", rule="The requested skill is not installed.")
            )
        raise typer.Exit(code=2)

    console.print(
        make_panel(
            "ok",
            "Remove Summary",
            [f"Removed skill '{name}'." for name in outcome.removed],
        )
    )


def main() -> None:
    """Run the Typer application."""
    app()


@env_app.command("list")
def list_envs(
    global_: bool = typer.Option(
        False,
        "--global",
        "-g",
        help="List globally stored environments instead of project-scoped ones.",
    ),
) -> None:
    """List project or reusable global environment manifests."""
    context = resolve_install_context(False)
    scope = "global" if global_ else "project"
    records = list_environments(context, scope=scope)

    table = Table(title=f"{scope.title()} Skill Environments")
    table.add_column("Name", style="bold")
    table.add_column("Active")
    table.add_column("Skills")

    for record in records:
        table.add_row(
            record.name,
            "yes" if record.active else "",
            str(record.skill_count),
        )

    if not records:
        table.caption = "No environments"
    console.print(table)


@env_app.command(no_args_is_help=True)
def create(
    name: str = typer.Argument(..., help="Name of the environment to create."),
    global_: bool = typer.Option(
        False,
        "--global",
        "-g",
        help="Store the manifest in ~/.trivium/environments for reuse across projects.",
    ),
) -> None:
    """Capture the current runtime as an environment manifest."""
    context = resolve_install_context(False)
    scope = "global" if global_ else "project"
    try:
        record = create_environment(context, name=name, scope=scope)
    except EnvironmentError as error:
        _print_environment_error(error)
        raise typer.Exit(code=error.exit_code) from error

    lines = [
        f"Created environment '{record.name}'.",
        f"Captured {record.skill_count} skill{'s' if record.skill_count != 1 else ''}.",
        f"Scope: {scope}.",
    ]
    console.print(make_panel("ok", "Environment Created", lines))


@env_app.command(no_args_is_help=True)
def activate(
    name: str = typer.Argument(..., help="Name of the environment to activate."),
    global_: bool = typer.Option(
        False,
        "--global",
        "-g",
        help="Activate a reusable manifest from ~/.trivium/environments.",
    ),
) -> None:
    """Activate a project or global manifest in the current project runtime."""
    context = resolve_install_context(False)
    scope = "global" if global_ else "project"
    try:
        activate_environment(context, name, scope=scope)
    except EnvironmentError as error:
        _print_environment_error(error)
        raise typer.Exit(code=error.exit_code) from error

    console.print(make_panel("ok", "Environment Activated", [f"Activated environment '{name}'."]))


@env_app.command()
def deactivate() -> None:
    """Restore the runtime that preceded environment activation."""
    context = resolve_install_context(False)
    try:
        was_active = deactivate_environment(context)
    except EnvironmentError as error:
        _print_environment_error(error)
        raise typer.Exit(code=error.exit_code) from error

    if not was_active:
        console.print(make_panel("info", "No Active Environment", ["Nothing to deactivate."]))
        return

    console.print(make_panel("ok", "Environment Deactivated", ["Restored the default runtime."]))


@env_app.command("remove", no_args_is_help=True)
def remove_env(
    name: str = typer.Argument(..., help="Name of the environment to remove."),
    global_: bool = typer.Option(
        False,
        "--global",
        "-g",
        help="Remove a reusable manifest from ~/.trivium/environments.",
    ),
) -> None:
    """Remove a named manifest from an explicit scope."""
    context = resolve_install_context(False)
    scope = "global" if global_ else "project"
    try:
        remove_environment(context, name, scope=scope)
    except EnvironmentError as error:
        _print_environment_error(error)
        raise typer.Exit(code=error.exit_code) from error

    lines = [f"Removed the {scope} environment '{name}'."]
    console.print(make_panel("ok", "Environment Removed", lines))


@env_app.command("info")
def info_env(
    name: str | None = typer.Argument(
        None, help="Optional environment name to inspect. Defaults to the active environment."
    ),
    global_: bool = typer.Option(
        False,
        "--global",
        "-g",
        help="Inspect a reusable manifest from ~/.trivium/environments.",
    ),
) -> None:
    """Show details for a project or global environment manifest."""
    context = resolve_install_context(False)
    scope = "global" if global_ else "project"
    details = describe_environment(context, name, scope=scope)
    if details is None:
        active = active_environment_name(context)
        if name is None and active is None:
            console.print(make_panel("info", "No Active Environment", ["No environment is currently active."]))
            return
        target = name or active or ""
        console.print(make_panel("err", "Environment Not Found", [f"No environment named '{target}' was found."]))
        raise typer.Exit(code=2)

    lines = [
        f"Name: {details.name}",
        f"Scope: {details.scope}",
        f"Active: {'yes' if details.active else 'no'}",
        f"Skills: {len(details.skill_names)}",
    ]
    if details.skill_names:
        lines.extend(f"- {skill_name}" for skill_name in details.skill_names)
    console.print(make_panel("info", "Environment Info", lines))


def _render_skill_list(*, json_: bool, global_: bool) -> None:
    context = resolve_install_context(global_)
    lockfile = load_lockfile(context.lockfile_path, expected_mode=context.mode)
    if json_:
        typer.echo(json.dumps(lockfile.to_dict(), indent=2, sort_keys=True, allow_nan=False))
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


def _select_add_conflict(skill_name: str) -> str | None:
    return questionary.select(
        f"Resolve conflict for '{skill_name}'",
        choices=["Keep existing", "Replace with new", "Skip"],
    ).ask()


def _confirm(prompt: str) -> bool:
    if _is_interactive_terminal():
        return bool(questionary.confirm(prompt, default=False).ask())

    response = sys.stdin.readline()
    if not response:
        console.print(make_panel("warn", "No Input", ["stdin reached EOF; treating as 'no'."]))
        return False
    return response.strip().lower() in {"y", "yes"}


def _print_environment_error(error: EnvironmentError) -> None:
    console.print(make_panel(error.kind, error.title, list(error.lines)))
