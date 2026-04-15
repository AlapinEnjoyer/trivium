from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import typer
from rich.panel import Panel

from skill_trivium.context import ensure_storage, resolve_install_context
from skill_trivium.git import GitCloneError, cloned_repo
from skill_trivium.lockfile import load_lockfile, write_lockfile
from skill_trivium.models import InstallContext, LockfileData, ParsedSkill, SkillLockEntry, ValidationIssue
from skill_trivium.skills import (
    build_lock_entry,
    discover_skills_path,
    enumerate_skill_directories,
    install_skill_tree,
    rewrite_normalized_skill_document_if_needed,
    utc_now,
    validate_skill_directory,
)
from skill_trivium.ui import console, make_panel, print_validation_issue


class ProgressLike(Protocol):
    """
    Protocol for progress indicator objects used in this workflow.

    Any object matching this interface can be used for progress reporting,
    including Rich Progress instances or test doubles. This allows
    dependency injection and easier testing.
    """

    def __enter__(self) -> "ProgressLike": ...

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None: ...

    def add_task(self, description: str, total: object = None) -> object: ...

    def update(self, task_id: object, completed: int) -> None: ...

    def stop(self) -> None: ...


@dataclass(slots=True)
class AddOutcome:
    installed: list[str] = field(default_factory=list)
    would_install: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)


@dataclass(slots=True)
class AddResolution:
    pending_installs: list[ParsedSkill] = field(default_factory=list)
    conflicts: list[tuple[ParsedSkill, SkillLockEntry]] = field(default_factory=list)


def run_add(
    *,
    ctx: typer.Context,
    url: str,
    all_: bool,
    skills: str | None,
    path: str | None,
    yes: bool,
    dry_run: bool,
    global_: bool,
    progress_factory: Callable[[], ProgressLike],
    is_interactive_terminal: Callable[[], bool],
    select_conflict: Callable[[str], str | None],
) -> None:
    """
    Execute the 'trv add' workflow for installing skills from a remote repository.

    Handles cloning the repository, selecting and validating skills, resolving conflicts,
    and updating the lockfile. Supports dry runs, interactive and non-interactive modes,
    and dependency injection for progress reporting and conflict resolution.

    Args:
        ctx (typer.Context): Typer context for CLI argument parsing.
        url (str): URL of the remote repository to clone.
        all_ (bool): Whether to add all available skills.
        skills (str | None): Comma-separated skill names to add, or None.
        path (str | None): Optional subdirectory path to search for skills.
        yes (bool): Whether to automatically resolve conflicts by replacing existing skills.
        dry_run (bool): If True, simulates the add operation without making changes.
        global_ (bool): Whether to install skills globally.
        progress_factory (Callable[[], ProgressLike]): Factory for creating a progress indicator.
        is_interactive_terminal (Callable[[], bool]): Function to check if terminal is interactive.
        select_conflict (Callable[[str], str | None]): Function to resolve skill conflicts.

    Returns:
        None

    Raises:
        typer.Exit: If an error occurs or user input is invalid.
    """
    requested_names = _parse_add_skill_names(ctx, all_, skills)
    context = resolve_install_context(global_)
    lockfile = load_lockfile(context.lockfile_path)

    install_outcome = AddOutcome()

    with progress_factory() as progress:
        task = progress.add_task("Cloning repository", total=None)
        try:
            with cloned_repo(url) as (repo_path, commit_hash):
                progress.update(task, completed=1)
                skills_container, skills_path = _resolve_skills_container(repo_path, path)
                target_directories = _select_target_skill_directories(
                    skills_container=skills_container,
                    requested_names=requested_names,
                    validation_issues=install_outcome.validation_issues,
                    failed=install_outcome.failed,
                )
                parsed_skills = _validate_target_skills(
                    target_directories,
                    validation_issues=install_outcome.validation_issues,
                    failed=install_outcome.failed,
                )
                resolution = _classify_add_candidates(
                    parsed_skills=parsed_skills,
                    lockfile=lockfile,
                    source_url=url,
                    context=context,
                    dry_run=dry_run,
                    repaired=install_outcome.repaired,
                    skipped=install_outcome.skipped,
                )

                # Stop the live display before printing conflict panels or
                # prompting so Rich and Questionary do not compete for the terminal.
                progress.stop()

                if resolution.conflicts and not yes and not is_interactive_terminal():
                    for parsed_skill, existing in resolution.conflicts:
                        console.print(_conflict_panel(parsed_skill, existing, url, commit_hash))
                    raise typer.Exit(code=4)

                replaced = _resolve_conflicts(
                    resolution=resolution,
                    yes=yes,
                    select_conflict=select_conflict,
                    incoming_source_url=url,
                    incoming_commit_hash=commit_hash,
                    skipped=install_outcome.skipped,
                )

                _apply_pending_installs(
                    pending_installs=resolution.pending_installs,
                    source_url=url,
                    commit_hash=commit_hash,
                    skills_path=skills_path,
                    context=context,
                    lockfile=lockfile,
                    dry_run=dry_run,
                    installed=install_outcome.installed,
                    would_install=install_outcome.would_install,
                )

                if resolution.pending_installs and not dry_run:
                    write_lockfile(context, lockfile)

                if replaced and not dry_run:
                    console.print(
                        make_panel(
                            "info",
                            "Conflicts Replaced",
                            [f"Replaced skill '{name}' with the incoming source." for name in sorted(replaced)],
                        )
                    )
                if install_outcome.repaired:
                    console.print(
                        make_panel(
                            "info",
                            "Normalized Installed Skills",
                            [
                                f"Rewrote installed SKILL.md for '{name}' to match normalized metadata."
                                for name in install_outcome.repaired
                            ],
                        )
                    )
        except GitCloneError as error:
            lines = [error.stderr]
            if error.guidance is not None:
                lines.append(error.guidance)
            console.print(make_panel("warn" if error.auth_failure else "err", "Git Clone Failed", lines))
            raise typer.Exit(code=5 if error.auth_failure else 1) from error

    summary_lines = _summary_lines(
        install_outcome.installed,
        install_outcome.would_install,
        install_outcome.skipped,
        install_outcome.failed,
    )
    if summary_lines:
        title = "Dry Run" if dry_run else "Add Summary"
        console.print(make_panel("ok" if not install_outcome.failed else "info", title, summary_lines))

    if install_outcome.validation_issues:
        raise typer.Exit(code=2)
    if dry_run and install_outcome.would_install:
        raise typer.Exit(code=3)


def _parse_add_skill_names(ctx: typer.Context, all_: bool, skills_value: str | None) -> list[str] | None:
    """
    Parse and validate skill name arguments for the add command.

    Ensures that either --all or --skills is used (but not both), and that
    the correct number and type of arguments are provided. Returns a list
    of requested skill names, or None if --all is used. Exits with an error
    and prints a message if arguments are invalid.
    """
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


def _resolve_skills_container(repo_path: Path, path: str | None) -> tuple[Path, str]:
    """
    Resolve the skills container directory within a cloned repository.

    Attempts to locate the directory containing skill definitions (with SKILL.md files)
    based on the provided repository path and optional subdirectory path. If the directory
    cannot be found or is invalid, prints an error panel and exits the program.

    Args:
        repo_path (Path): The root path of the cloned repository.
        path (str | None): Optional subdirectory path to search for skills.

    Returns:
        tuple[Path, str]: The resolved skills container path and its string representation.

    Raises:
        typer.Exit: If the skills container cannot be found or the path is invalid.
    """
    resolved = discover_skills_path(repo_path, path)
    if resolved is not None:
        return resolved

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


def _select_target_skill_directories(
    *,
    skills_container: Path,
    requested_names: list[str] | None,
    validation_issues: list[ValidationIssue],
    failed: list[str],
) -> list[Path]:
    """
    Select and validate target skill directories from the skills container.

    If specific skill names are requested, checks for their existence in the skills container.
    Records validation issues and failed names for any missing skills. If no names are requested,
    returns all candidate skill directories.

    Args:
        skills_container (Path): Path to the directory containing available skills.
        requested_names (list[str] | None): List of skill names to select, or None to select all.
        validation_issues (list[ValidationIssue]): List to append validation issues for missing skills.
        failed (list[str]): List to append names of skills that were not found.

    Returns:
        list[Path]: List of Paths to the selected skill directories.
    """
    candidates = enumerate_skill_directories(skills_container)
    if requested_names is None:
        return candidates

    candidate_map = {candidate.name: candidate for candidate in candidates}
    missing_names = [name for name in requested_names if name not in candidate_map]
    for name in missing_names:
        issue = ValidationIssue(
            skill_name=name,
            field="name",
            rule="The requested skill was not found in the remote repository.",
        )
        validation_issues.append(issue)
        print_validation_issue(issue)
        failed.append(name)
    return [candidate_map[name] for name in requested_names if name in candidate_map]


def _validate_target_skills(
    target_directories: list[Path],
    *,
    validation_issues: list[ValidationIssue],
    failed: list[str],
) -> list[ParsedSkill]:
    """
    Validate target skill directories and parse them into ParsedSkill objects.

    Args:
        target_directories (list[Path]): List of skill directories to validate.
        validation_issues (list[ValidationIssue]): List to append validation issues for invalid skills.
        failed (list[str]): List to append names of skills that failed validation.

    Returns:
        list[ParsedSkill]: List of successfully parsed skills.
    """
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
    return parsed_skills


def _classify_add_candidates(
    *,
    parsed_skills: list[ParsedSkill],
    lockfile: LockfileData,
    source_url: str,
    context: InstallContext,
    dry_run: bool,
    repaired: list[str],
    skipped: dict[str, str],
) -> AddResolution:
    """
    Classify parsed skills into pending installs, conflicts, and skipped categories.

    Args:
        parsed_skills (list[ParsedSkill]): List of parsed skills to classify.
        lockfile (LockfileData): Current lockfile data.
        source_url (str): Source URL of the incoming skills.
        context (InstallContext): Installation context.
        dry_run (bool): Whether this is a dry run.
        repaired (list[str]): List to append names of repaired skills.
        skipped (dict[str, str]): Dictionary to append names and reasons for skipped skills.

    Returns:
        AddResolution: Resolution object containing classified skills.
    """

    resolution = AddResolution()
    for parsed_skill in parsed_skills:
        existing = lockfile.skills.get(parsed_skill.name)
        if existing is None:
            resolution.pending_installs.append(parsed_skill)
            continue
        if existing.source_url == source_url:
            if not dry_run and rewrite_normalized_skill_document_if_needed(
                parsed_skill, context.install_path_for(parsed_skill.name)
            ):
                repaired.append(parsed_skill.name)
                _print_conversion_warnings(parsed_skill)
            skipped[parsed_skill.name] = "already installed from the same source"
            continue
        resolution.conflicts.append((parsed_skill, existing))
    return resolution


def _resolve_conflicts(
    *,
    resolution: AddResolution,
    yes: bool,
    select_conflict: Callable[[str], str | None],
    incoming_source_url: str,
    incoming_commit_hash: str,
    skipped: dict[str, str],
) -> list[str]:
    """
    Resolve conflicts between incoming skills and existing skills in the lockfile.

    Args:
        resolution (AddResolution): Resolution object containing classified skills.
        yes (bool): Whether to automatically resolve conflicts by replacing existing skills.
        select_conflict (Callable[[str], str | None]): Function to select conflict resolution for a skill.
        incoming_source_url (str): Source URL of the incoming skills.
        incoming_commit_hash (str): Commit hash of the incoming skills.
        skipped (dict[str, str]): Dictionary to append names and reasons for skipped skills.

    Returns:
        list[str]: List of names of skills that were replaced.
    """
    replaced: list[str] = []
    if yes:
        for parsed_skill, _existing in resolution.conflicts:
            resolution.pending_installs.append(parsed_skill)
            replaced.append(parsed_skill.name)
            skipped.pop(parsed_skill.name, None)
        return replaced

    for parsed_skill, existing in resolution.conflicts:
        console.print(_conflict_panel(parsed_skill, existing, incoming_source_url, incoming_commit_hash))
        choice = select_conflict(parsed_skill.name)
        if choice == "Replace with new":
            resolution.pending_installs.append(parsed_skill)
            replaced.append(parsed_skill.name)
        elif choice == "Skip":
            skipped[parsed_skill.name] = "skipped"
        else:
            skipped[parsed_skill.name] = "kept existing"
    return replaced


def _apply_pending_installs(
    *,
    pending_installs: list[ParsedSkill],
    source_url: str,
    commit_hash: str,
    skills_path: str,
    context: InstallContext,
    lockfile: LockfileData,
    dry_run: bool,
    installed: list[str],
    would_install: list[str],
) -> None:
    for parsed_skill in pending_installs:
        entry = build_lock_entry(
            parsed_skill=parsed_skill,
            source_url=source_url,
            commit_hash=commit_hash,
            skills_path=skills_path,
            context=context,
            installed_at=utc_now(),
        )
        if dry_run:
            would_install.append(parsed_skill.name)
            continue
        ensure_storage(context)
        destination = context.install_path_for(parsed_skill.name)
        install_skill_tree(parsed_skill, destination)
        _print_conversion_warnings(parsed_skill)
        lockfile.skills[parsed_skill.name] = entry
        installed.append(parsed_skill.name)


def _print_conversion_warnings(parsed_skill: ParsedSkill) -> None:
    for warning in parsed_skill.warnings:
        console.print(make_panel("warn", f"Conversion Warning: {parsed_skill.name}", [warning]))


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
