"""Implement concurrent refreshes of skills recorded in a lockfile.

Sources are grouped so each repository is cloned once, then validation,
content comparison, installation, lockfile updates, and active-environment
synchronization are applied according to the requested or dry-run mode.
"""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from skill_trivium.context import ensure_storage
from skill_trivium.environment import (
    ensure_active_environment_runtime_is_clean,
    sync_active_environment,
)
from skill_trivium.git import GitCloneError, cloned_repo
from skill_trivium.lockfile import installation_lock, load_lockfile, write_lockfile
from skill_trivium.models import (
    InstallContext,
    LockfileData,
    ParsedSkill,
    SkillLockEntry,
    SourceUpdateResult,
    UpdateWarning,
    ValidationIssue,
)
from skill_trivium.skills import (
    build_lock_entry,
    hash_parsed_skill,
    hash_skill_directory,
    install_skill_tree,
    rewrite_normalized_skill_document_if_needed,
    validate_skill_directory,
)
from skill_trivium.ui import console, make_panel, print_validation_issue, progress_bar, shorten_source


@dataclass(slots=True)
class UpdateOutcome:
    """Collect update changes, warnings, and the resulting CLI status."""

    updated_names: list[str] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)
    auth_failure: bool = False
    general_errors: bool = False
    warning_count: int = 0
    lockfile_changed: bool = False
    runtime_changed: bool = False
    nothing_installed: bool = False
    missing_names: tuple[str, ...] = ()

    def exit_code(self, *, dry_run: bool) -> int | None:
        """Return the CLI exit code implied by this outcome."""
        if self.auth_failure:
            return 5
        if self.validation_issues or self.missing_names:
            return 2
        if self.general_errors:
            return 1
        if dry_run and self.updated_names:
            return 3
        return None


def run_update(
    *,
    context: InstallContext,
    requested_skills: list[str],
    dry_run: bool,
) -> UpdateOutcome:
    """Refresh requested lockfile entries from their source repositories.

    Entries are grouped by source so each repository is cloned once. Changed
    skills are revalidated and installed unless this is a dry run; refreshed
    revisions and runtime changes are reflected in the returned outcome.

    Args:
        context: Installation context containing the runtime and destination.
        requested_skills: Names to update, or an empty list for all entries.
        dry_run: Whether to report changes without writing files.

    Returns:
        An outcome containing updated names, warnings, errors, and change flags.

    Raises:
        EnvironmentError: If an active environment runtime is not clean enough
            to update safely.
    """
    if dry_run:
        return _run_update(context=context, requested_skills=requested_skills, dry_run=True)

    with installation_lock(context):
        return _run_update(context=context, requested_skills=requested_skills, dry_run=False)


def _run_update(
    *,
    context: InstallContext,
    requested_skills: list[str],
    dry_run: bool,
) -> UpdateOutcome:
    lockfile = load_lockfile(context.lockfile_path, expected_mode=context.mode)
    if not lockfile.skills:
        return UpdateOutcome(nothing_installed=True)

    missing_names = tuple(name for name in requested_skills if name not in lockfile.skills)
    if missing_names:
        return UpdateOutcome(missing_names=missing_names)

    ensure_active_environment_runtime_is_clean(context)
    target_entries = {name: lockfile.skills[name] for name in (requested_skills or sorted(lockfile.skills))}
    grouped_entries: dict[str, list[SkillLockEntry]] = defaultdict(list)
    for entry in target_entries.values():
        grouped_entries[entry.source_url].append(entry)

    outcome = UpdateOutcome()
    with progress_bar() as progress:
        future_map = {}
        with ThreadPoolExecutor(max_workers=max(1, min(8, len(grouped_entries)))) as executor:
            for source_url, entries in sorted(grouped_entries.items()):
                task_id = progress.add_task(f"Updating {shorten_source(source_url, 48)}", total=None)
                future = executor.submit(_update_source_group, entries, context, dry_run)
                future_map[future] = (source_url, task_id)

            for future in as_completed(future_map):
                source_url, task_id = future_map[future]
                result = future.result()
                progress.update(task_id, completed=1)
                _apply_update_result(
                    lockfile=lockfile,
                    result=result,
                    dry_run=dry_run,
                    outcome=outcome,
                )

    if outcome.lockfile_changed and not dry_run:
        write_lockfile(context, lockfile)

    if outcome.runtime_changed and not dry_run:
        sync_active_environment(context)

    return outcome


def render_update_summary(outcome: UpdateOutcome, *, dry_run: bool) -> None:
    """Render the user-facing summary for an update operation."""
    if dry_run and outcome.updated_names:
        console.print(
            make_panel(
                "info",
                "Dry Run",
                [f"Would update skill '{name}'." for name in sorted(outcome.updated_names)],
            )
        )
        return

    if outcome.updated_names:
        console.print(
            make_panel(
                "ok",
                "Update Summary",
                [f"Updated skill '{name}'." for name in sorted(outcome.updated_names)],
            )
        )
        return

    if (
        not outcome.validation_issues
        and not outcome.auth_failure
        and not outcome.general_errors
        and outcome.warning_count == 0
    ):
        console.print(make_panel("ok", "Up To Date", ["All requested skills are already current."]))


def _apply_update_result(
    *,
    lockfile: LockfileData,
    result: SourceUpdateResult,
    dry_run: bool,
    outcome: UpdateOutcome,
) -> None:
    for warning in result.warnings:
        outcome.warning_count += 1
        lines = [warning.message]
        if warning.guidance is not None:
            lines.append(warning.guidance)
        console.print(
            make_panel(
                "warn",
                f"Update Warning: {warning.skill_name}",
                lines,
            )
        )

    for issue in result.validation_issues:
        outcome.validation_issues.append(issue)
        print_validation_issue(issue)

    for error in result.errors:
        outcome.general_errors = True
        console.print(make_panel("err", "Update Failed", [error]))

    outcome.auth_failure = outcome.auth_failure or result.auth_failure
    outcome.runtime_changed = outcome.runtime_changed or bool(result.rewritten)

    for skill_name, refreshed_entry in sorted(result.refreshed.items()):
        lockfile.skills[skill_name] = refreshed_entry
        outcome.lockfile_changed = True

    if dry_run:
        outcome.updated_names.extend(sorted(result.updated))
        return

    for skill_name, new_entry in sorted(result.updated.items()):
        lockfile.skills[skill_name] = new_entry
        outcome.updated_names.append(skill_name)
        outcome.lockfile_changed = True
        outcome.runtime_changed = True


def _update_source_group(
    entries: list[SkillLockEntry],
    context: InstallContext,
    dry_run: bool,
) -> SourceUpdateResult:
    result = SourceUpdateResult()
    try:
        source_url = entries[0].source_url
        with cloned_repo(source_url) as (repo_path, commit_hash):
            for entry in entries:
                container = repo_path if entry.skills_path == "." else repo_path / entry.skills_path
                if not container.is_dir():
                    result.warnings.append(
                        UpdateWarning(
                            skill_name=entry.name,
                            message=f"The registered skills path '{entry.skills_path}' no longer exists for this skill.",
                            guidance=f"Re-run `trivium add {source_url}` if the skill moved elsewhere in the repository.",
                        )
                    )
                    continue

                skill_dir = container / entry.name
                if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
                    result.warnings.append(
                        UpdateWarning(
                            skill_name=entry.name,
                            message=f"The skill was not found at '{entry.skills_path}/{entry.name}'.",
                            guidance=f"Re-run `trivium add {source_url}` if the skill moved elsewhere in the repository.",
                        )
                    )
                    continue

                parsed_skill, issues = validate_skill_directory(skill_dir)
                if issues:
                    result.validation_issues.extend(issues)
                    continue

                destination = context.install_path_for(entry.name)
                if parsed_skill and not _entry_needs_refresh(entry, parsed_skill, destination):
                    if not dry_run:
                        try:
                            if rewrite_normalized_skill_document_if_needed(parsed_skill, destination):
                                result.rewritten.add(parsed_skill.name)
                                # Use extend with a generator to avoid a manual loop
                                result.warnings.extend(
                                    UpdateWarning(skill_name=parsed_skill.name, message=warning)
                                    for warning in parsed_skill.warnings
                                )
                        except OSError as error:
                            result.errors.append(f"{entry.name}: {error}")
                            continue

                    needs_lock_update = entry.content_hash is None or entry.commit_hash != commit_hash

                    if needs_lock_update:
                        result.refreshed[entry.name] = build_lock_entry(
                            parsed_skill=parsed_skill,
                            source_url=entry.source_url,
                            commit_hash=commit_hash,
                            skills_path=entry.skills_path,
                            context=context,
                            installed_at=entry.installed_at,
                        )

                    continue

                if parsed_skill:
                    updated_entry = build_lock_entry(
                        parsed_skill=parsed_skill,
                        source_url=entry.source_url,
                        commit_hash=commit_hash,
                        skills_path=entry.skills_path,
                        context=context,
                        installed_at=entry.installed_at,
                    )
                    if not dry_run:
                        try:
                            ensure_storage(context)
                            install_skill_tree(parsed_skill, destination)
                            for warning in parsed_skill.warnings:
                                result.warnings.append(UpdateWarning(skill_name=parsed_skill.name, message=warning))
                        except OSError as error:
                            result.errors.append(f"{entry.name}: {error}")
                            continue
                    result.updated[entry.name] = updated_entry
    except GitCloneError as error:
        result.auth_failure = error.auth_failure
        message = error.stderr
        if error.guidance is not None:
            message = f"{message} {error.guidance}"
        result.errors.append(f"{entries[0].source_url}: {message}")

    return result


def _entry_needs_refresh(entry: SkillLockEntry, parsed_skill: ParsedSkill, destination: Path) -> bool:
    expected_hash = hash_parsed_skill(parsed_skill)
    if entry.content_hash is None:
        if not destination.is_dir():
            return True
        return hash_skill_directory(destination) != expected_hash
    if expected_hash != entry.content_hash:
        return True
    return not destination.is_dir()
