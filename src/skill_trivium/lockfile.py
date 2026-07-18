"""Load, render, and atomically write skill lockfiles.

Lockfiles preserve source revisions, normalized skill metadata, installation
paths, and content hashes so commands can reproduce and verify the runtime
without mutating in-memory models during serialization.
"""

import os
import sys
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO, Literal

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

import click
import tomli_w

from skill_trivium.models import InstallContext, LockfileData, SkillLockEntry
from skill_trivium.skills import utc_now

LOCKFILE_VERSION = 1


class LockfileError(click.ClickException):
    """Report malformed or incompatible lockfile contents."""

    pass


def load_lockfile(
    lockfile_path: Path,
    *,
    expected_mode: Literal["project", "global"] | None = None,
) -> LockfileData:
    """Load and validate a TOML lockfile.

    Args:
        lockfile_path: Path to the lockfile. A missing file represents an empty
            lockfile.
        expected_mode: Optional installation mode that the lockfile must use.

    Returns:
        Parsed lockfile metadata and skill entries.

    Raises:
        LockfileError: If TOML is malformed, sections have the wrong shape,
            the version is unsupported, or the mode does not match.
    """
    if not lockfile_path.exists():
        return LockfileData()
    try:
        with lockfile_path.open("rb") as file:
            payload = tomllib.load(file)
    except tomllib.TOMLDecodeError as error:
        raise LockfileError(f"Invalid lockfile '{lockfile_path}': {error}") from error

    raw_meta = payload.get("meta", {})
    if not isinstance(raw_meta, dict):
        raise LockfileError(f"Invalid lockfile '{lockfile_path}': 'meta' must be a table.")
    meta = dict(raw_meta)
    if type(meta.get("version")) is not int or meta.get("version") != LOCKFILE_VERSION:
        raise LockfileError(
            f"Unsupported lockfile version in '{lockfile_path}': expected {LOCKFILE_VERSION}, "
            f"found {meta.get('version')!r}."
        )
    if expected_mode is not None and meta.get("mode") != expected_mode:
        raise LockfileError(
            f"Lockfile mode mismatch in '{lockfile_path}': expected '{expected_mode}', found {meta.get('mode')!r}."
        )

    raw_skills = payload.get("skills", {})
    if not isinstance(raw_skills, dict):
        raise LockfileError(f"Invalid lockfile '{lockfile_path}': 'skills' must be a table.")
    skills: dict[str, SkillLockEntry] = {}
    for name, entry in raw_skills.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            raise LockfileError(f"Invalid lockfile '{lockfile_path}': each skill must be a table.")
        skills[name] = SkillLockEntry.from_dict(name, entry)
    return LockfileData(meta=meta, skills=skills)


def write_lockfile(context: InstallContext, lockfile: LockfileData) -> None:
    """Write a context lockfile with version, mode, and update metadata.

    Args:
        context: Installation context that determines the destination and mode.
        lockfile: Lockfile data to serialize.
    """
    write_lockfile_path(
        context.lockfile_path,
        lockfile,
        meta_updates={"version": LOCKFILE_VERSION, "mode": context.mode, "updated_at": utc_now()},
    )


def render_lockfile(lockfile: LockfileData, *, meta_updates: Mapping[str, object] | None = None) -> str:
    """Render lockfile data as deterministic TOML without mutating it.

    Args:
        lockfile: Lockfile data to serialize.
        meta_updates: Metadata values to overlay in the rendered document.

    Returns:
        TOML text with skill tables ordered by skill name.
    """
    payload = lockfile.to_dict()
    payload["meta"] = {**lockfile.meta, **(meta_updates or {})}
    return tomli_w.dumps(payload)


def write_lockfile_path(
    lockfile_path: Path,
    lockfile: LockfileData,
    *,
    meta_updates: Mapping[str, object] | None = None,
) -> None:
    """Atomically write lockfile data to a specific path.

    Args:
        lockfile_path: Destination lockfile path.
        lockfile: Lockfile data to serialize.
        meta_updates: Metadata values to overlay in the rendered document.

    Raises:
        OSError: If the temporary file or replacement cannot be written.
    """
    lockfile_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=lockfile_path.parent, prefix=f".{lockfile_path.name}.", delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(render_lockfile(lockfile, meta_updates=meta_updates))
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        temporary_path.replace(lockfile_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


@contextmanager
def exclusive_file_lock(lock_path: Path) -> Iterator[None]:
    """Acquire and release an exclusive lock on the given path."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        _lock_file(lock_file)
        try:
            yield
        finally:
            _unlock_file(lock_file)


def _lock_file(lock_file: BinaryIO) -> None:
    """Acquire a platform-native exclusive lock."""
    if sys.platform == "win32":
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def _unlock_file(lock_file: BinaryIO) -> None:
    """Release a platform-native exclusive lock."""
    if sys.platform == "win32":
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
