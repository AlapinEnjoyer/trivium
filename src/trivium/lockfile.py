import tomllib
from pathlib import Path

import tomli_w

from trivium.models import InstallContext, LockfileData, SkillLockEntry
from trivium.skills import utc_now


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


def ensure_lockfile(context: InstallContext) -> LockfileData:
    lockfile = load_lockfile(context.lockfile_path)
    if context.lockfile_path.exists():
        return lockfile
    write_lockfile(context, lockfile)
    return lockfile


def write_lockfile(context: InstallContext, lockfile: LockfileData) -> None:
    context.lockfile_path.parent.mkdir(parents=True, exist_ok=True)
    payload = lockfile.to_dict()
    meta = dict(lockfile.meta)
    meta.update(
        {
            "version": 1,
            "mode": context.mode,
            "updated_at": utc_now(),
        }
    )
    payload["meta"] = meta
    context.lockfile_path.write_text(tomli_w.dumps(payload), encoding="utf-8")
