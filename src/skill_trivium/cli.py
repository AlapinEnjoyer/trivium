import json
import shutil
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
from skill_trivium.context import ensure_storage, resolve_install_context
from skill_trivium.lockfile import load_lockfile, write_lockfile
from skill_trivium.models import ValidationIssue
from skill_trivium.skills import (
    build_skill_markdown,
    parse_skill_document,
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
    run_add(
        ctx=ctx,
        url=url,
        all_=all_,
        skills=skills,
        path=path,
        yes=yes,
        dry_run=dry_run,
        global_=global_,
        progress_factory=progress_bar,
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


def _validate_init_name(skill_name: str) -> ValidationIssue | None:
    issues = validate_skill_name(skill_name, skill_name=skill_name)
    if not issues:
        return None
    return issues[0]
