import tomllib
from collections.abc import Mapping
from pathlib import Path

import tomli_w

from skill_trivium.models import InstallContext, LockfileData, SkillLockEntry
from skill_trivium.skills import utc_now

LOCKFILE_VERSION = 1


def load_lockfile(lockfile_path: Path) -> LockfileData:
    if not lockfile_path.exists():
        return LockfileData()

    with lockfile_path.open("rb") as file:
        payload = tomllib.load(file)

    raw_meta = payload.get("meta", {})
    meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}

    skills: dict[str, SkillLockEntry] = {}
    raw_skills = payload.get("skills", {})
    if isinstance(raw_skills, dict):
        for name, entry in raw_skills.items():
            if isinstance(name, str) and isinstance(entry, dict):
                skills[name] = SkillLockEntry.from_dict(name, entry)

    return LockfileData(meta=meta, skills=skills)


def write_lockfile(context: InstallContext, lockfile: LockfileData) -> None:
    write_lockfile_path(
        context.lockfile_path,
        lockfile,
        meta_updates={
            "version": LOCKFILE_VERSION,
            "mode": context.mode,
            "updated_at": utc_now(),
        },
    )


def render_lockfile(lockfile: LockfileData, *, meta_updates: Mapping[str, object] | None = None) -> str:
    payload = lockfile.to_dict()
    meta = dict(lockfile.meta)
    if meta_updates is not None:
        meta.update(meta_updates)
    payload["meta"] = meta
    return tomli_w.dumps(payload)


def write_lockfile_path(
    lockfile_path: Path,
    lockfile: LockfileData,
    *,
    meta_updates: Mapping[str, object] | None = None,
) -> None:
    lockfile_path.parent.mkdir(parents=True, exist_ok=True)
    lockfile_path.write_text(render_lockfile(lockfile, meta_updates=meta_updates), encoding="utf-8")
