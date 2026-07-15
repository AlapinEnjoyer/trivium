"""Provide rollback for managed runtime changes before lockfile commit."""

import shutil
from pathlib import Path
from tempfile import mkdtemp
from types import TracebackType
from typing import Self

from skill_trivium.models import InstallContext


class RuntimeMutation:
    """Preserve a runtime skill tree until its lockfile commit succeeds."""

    def __init__(self, context: InstallContext) -> None:
        """Initialize a mutation scope for an installation context."""
        self._context = context
        self._temporary_path: Path | None = None
        self._backup_path: Path | None = None
        self._committed = False

    def __enter__(self) -> Self:
        """Copy the current runtime into same-filesystem temporary storage."""
        self._context.skills_dir.parent.mkdir(parents=True, exist_ok=True)
        self._temporary_path = Path(
            mkdtemp(
                prefix=".trivium-mutation-",
                dir=self._context.skills_dir.parent,
            )
        )
        self._backup_path = self._temporary_path / "skills"
        try:
            if self._context.skills_dir.exists():
                shutil.copytree(self._context.skills_dir, self._backup_path)
        except OSError:
            shutil.rmtree(self._temporary_path, ignore_errors=True)
            raise
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Restore uncommitted changes and clean temporary storage."""
        if not self._committed:
            self._restore()
        if self._temporary_path is not None:
            shutil.rmtree(self._temporary_path, ignore_errors=True)

    def commit(self) -> None:
        """Keep current runtime changes after the lockfile was committed."""
        self._committed = True

    def _restore(self) -> None:
        if self._temporary_path is None:
            return
        failed_runtime = self._temporary_path / "failed-runtime"
        if self._context.skills_dir.exists():
            self._context.skills_dir.replace(failed_runtime)
        try:
            if self._backup_path is not None and self._backup_path.exists():
                self._backup_path.replace(self._context.skills_dir)
        except OSError:
            if failed_runtime.exists():
                failed_runtime.replace(self._context.skills_dir)
            raise
