from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self

Mode = Literal["project", "global"]


@dataclass(frozen=True, slots=True)
class InstallContext:
    mode: Mode
    base_dir: Path
    skills_dir: Path
    lockfile_path: Path
    install_prefix: Path

    def install_path_for(self, skill_name: str) -> Path:
        return self.skills_dir / skill_name

    def relative_install_path(self, skill_name: str) -> str:
        return (self.install_prefix / skill_name).as_posix()


@dataclass(slots=True)
class SkillLockEntry:
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
        raw_metadata = data.get("metadata")
        metadata: dict[str, str] | None
        if isinstance(raw_metadata, dict):
            metadata = {str(key): str(value) for key, value in raw_metadata.items()}
        else:
            metadata = None

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
        data: dict[str, object] = {
            "source_url": self.source_url,
            "commit_hash": self.commit_hash,
            "skills_path": self.skills_path,
            "install_path": self.install_path,
            "description": self.description,
            "installed_at": self.installed_at,
        }
        if self.content_hash is not None:
            data["content_hash"] = self.content_hash
        if self.license is not None:
            data["license"] = self.license
        if self.compatibility is not None:
            data["compatibility"] = self.compatibility
        if self.allowed_tools is not None:
            data["allowed_tools"] = self.allowed_tools
        if self.metadata is not None:
            data["metadata"] = self.metadata
        return data


@dataclass(slots=True)
class LockfileData:
    meta: dict[str, object] = field(default_factory=dict)
    skills: dict[str, SkillLockEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "meta": self.meta,
            "skills": {name: self.skills[name].to_toml_dict() for name in sorted(self.skills)},
        }


@dataclass(frozen=True, slots=True)
class ParsedSkill:
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
    skill_name: str
    field: str
    rule: str


@dataclass(frozen=True, slots=True)
class UpdateWarning:
    skill_name: str
    message: str
    guidance: str | None = None


@dataclass(slots=True)
class SourceUpdateResult:
    refreshed: dict[str, SkillLockEntry] = field(default_factory=dict)
    updated: dict[str, SkillLockEntry] = field(default_factory=dict)
    rewritten: set[str] = field(default_factory=set)
    warnings: list[UpdateWarning] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    auth_failure: bool = False


def _optional_string(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None
