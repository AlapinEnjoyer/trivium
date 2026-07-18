"""Define the typed records exchanged by Skill Trivium workflows.

The models cover installation locations, lockfile entries, parsed skill
documents, validation diagnostics, and update results. Serialization helpers
live on the lockfile models so file-format details stay out of command code.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self

Mode = Literal["project", "global"]


@dataclass(frozen=True, slots=True)
class InstallContext:
    """Describe where a project or global installation stores its files."""

    mode: Mode
    base_dir: Path
    skills_dir: Path
    lockfile_path: Path
    install_prefix: Path

    def install_path_for(self, skill_name: str) -> Path:
        """Return the on-disk installation path for a validated skill name."""
        _validate_install_name(skill_name)
        return self.skills_dir / skill_name

    def relative_install_path(self, skill_name: str) -> str:
        """Return the lockfile-relative installation path for a skill."""
        _validate_install_name(skill_name)
        return (self.install_prefix / skill_name).as_posix()


@dataclass(slots=True)
class SkillLockEntry:
    """Record the source and installed metadata for one skill."""

    name: str
    source_url: str
    commit_hash: str
    skills_path: str
    install_path: str
    description: str
    installed_at: str
    content_hash: str | None = None
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = None
    metadata: dict[str, str] | None = None

    @classmethod
    def from_dict(cls, name: str, data: dict[str, object]) -> Self:
        """Build a lock entry from a decoded TOML skill table."""
        raw_metadata = data.get("metadata")
        metadata = (
            {str(key): str(value) for key, value in raw_metadata.items()} if isinstance(raw_metadata, dict) else None
        )
        return cls(
            name=name,
            source_url=str(data.get("source_url", "")),
            commit_hash=str(data.get("commit_hash", "")),
            content_hash=_optional_string(data.get("content_hash")),
            skills_path=str(data.get("skills_path", ".")),
            install_path=str(data.get("install_path", "")),
            description=str(data.get("description", "")),
            installed_at=str(data.get("installed_at", "")),
            license=_optional_string(data.get("license")),
            compatibility=_optional_string(data.get("compatibility")),
            allowed_tools=_optional_string(data.get("allowed_tools")),
            metadata=metadata,
        )

    def to_toml_dict(self) -> dict[str, object]:
        """Serialize this entry to a TOML-compatible mapping."""
        data: dict[str, object] = {
            "source_url": self.source_url,
            "commit_hash": self.commit_hash,
            "skills_path": self.skills_path,
            "install_path": self.install_path,
            "description": self.description,
            "installed_at": self.installed_at,
        }
        for key in ("content_hash", "license", "compatibility", "allowed_tools", "metadata"):
            if (value := getattr(self, key)) is not None:
                data[key] = value
        return data


@dataclass(slots=True)
class LockfileData:
    """Represent lockfile metadata and its installed skill entries."""

    meta: dict[str, object] = field(default_factory=dict)
    skills: dict[str, SkillLockEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Serialize the lockfile with skill names in deterministic order."""
        return {"meta": self.meta, "skills": {name: self.skills[name].to_toml_dict() for name in sorted(self.skills)}}


@dataclass(frozen=True, slots=True)
class ParsedSkill:
    """Hold validated and normalized skill document data."""

    directory: Path
    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = None
    metadata: dict[str, str] | None = None
    frontmatter: dict[str, object] = field(default_factory=dict)
    body: str = ""
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """Describe one validation failure for a skill."""

    skill_name: str
    field: str
    rule: str


@dataclass(frozen=True, slots=True)
class UpdateWarning:
    """Describe a non-fatal issue found while refreshing a skill."""

    skill_name: str
    message: str
    guidance: str | None = None


@dataclass(slots=True)
class SourceUpdateResult:
    """Collect the results of updating skills from one source repository."""

    refreshed: dict[str, SkillLockEntry] = field(default_factory=dict)
    updated: dict[str, SkillLockEntry] = field(default_factory=dict)
    warnings: list[UpdateWarning] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    auth_failure: bool = False


def _optional_string(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None


def _validate_install_name(skill_name: str) -> None:
    path = Path(skill_name)
    if not skill_name or path.is_absolute() or len(path.parts) != 1 or skill_name in {".", ".."}:
        raise ValueError("Skill names must be a single relative path component.")
