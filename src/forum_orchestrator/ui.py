from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

console = Console()


@contextmanager
def progress_bar(disable: bool = False) -> Iterator[Progress]:
    p = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.fields[host]:<12}"),
        TextColumn("[white]{task.description}"),
        BarColumn(bar_width=None),
        TextColumn("{task.percentage:>3.0f}%"),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
        disable=disable,
    )
    with p:
        yield p


def add_task(p: Progress, description: str, host: str, total: int | None = None) -> TaskID:
    return p.add_task(description, host=host, total=total or 0)
