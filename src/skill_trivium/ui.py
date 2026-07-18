"""Centralize Rich presentation for status panels, progress, and truncation.

Commands use these helpers to keep colors, labels, validation errors, and
terminal progress consistent without embedding rendering details in the
installation workflows.
"""

from collections.abc import Sequence

from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.text import Text

from skill_trivium.models import ValidationIssue

console = Console()

STATUS_STYLES = {
    "ok": "green",
    "info": "blue",
    "warn": "yellow",
    "err": "red",
}


def make_panel(kind: str, title: str, lines: Sequence[str]) -> Panel:
    """Create a styled status panel from a status kind and message lines."""
    color = STATUS_STYLES[kind]
    renderables = [status_line(kind, line) for line in lines]
    return Panel(Group(*renderables), title=title, border_style=color)


def print_validation_issue(issue: ValidationIssue) -> None:
    """Print a validation issue in the standard error panel format."""
    console.print(
        make_panel(
            "err",
            f"Validation Failed: {issue.skill_name}",
            [f"Field: {issue.field}", f"Rule: {issue.rule}"],
        )
    )


def status_line(kind: str, message: str) -> Text:
    """Create a colored status line for a message."""
    text = Text()
    text.append(f"[{kind.upper()}] ", style=f"bold {STATUS_STYLES[kind]}")
    text.append(message)
    return text


def progress_bar() -> Progress:
    """Create the configured Rich progress display."""
    return Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        TimeElapsedColumn(),
        transient=True,
        console=console,
    )


def shorten_source(source_url: str, width: int = 36) -> str:
    """Shorten a repository URL for progress display."""
    return _truncate(source_url, width)


def truncate_text(value: str, width: int = 60) -> str:
    """Normalize whitespace and truncate text for terminal display."""
    return _truncate(" ".join(value.split()), width)


def _truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return "." * width
    return f"{value[: width - 3]}..."
