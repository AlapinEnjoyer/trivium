from collections.abc import Sequence

from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.text import Text

console = Console()

STATUS_STYLES = {
    "ok": "green",
    "info": "blue",
    "warn": "yellow",
    "err": "red",
}


def make_panel(kind: str, title: str, lines: Sequence[str]) -> Panel:
    color = STATUS_STYLES[kind]
    renderables = [status_line(kind, line) for line in lines]
    return Panel(Group(*renderables), title=title, border_style=color)


def status_line(kind: str, message: str) -> Text:
    text = Text()
    text.append(f"[{kind.upper()}] ", style=f"bold {STATUS_STYLES[kind]}")
    text.append(message)
    return text


def progress_bar() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        TimeElapsedColumn(),
        transient=True,
        console=console,
    )


def shorten_source(source_url: str, width: int = 36) -> str:
    return _truncate(source_url, width)


def truncate_text(value: str, width: int = 60) -> str:
    return _truncate(" ".join(value.split()), width)


def _truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return "." * width
    return f"{value[: width - 3]}..."
