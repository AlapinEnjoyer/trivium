import re
from datetime import UTC, datetime
from pathlib import Path

import yaml

from trivium.models import ParsedSkill, ValidationIssue

MAX_NAME_LENGTH = 64
NAME_PATTERN = re.compile(r"^[a-z0-9]$|^[a-z0-9](?:[a-z0-9]|-(?!-)){0,62}[a-z0-9]$")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def discover_skills_path(repo_root: Path, explicit_path: str | None) -> tuple[Path, str] | None:
    if explicit_path is not None:
        candidate = _resolve_repo_path(repo_root, explicit_path)
        if candidate is None or not candidate.is_dir():
            return None
        if not enumerate_skill_directories(candidate):
            return None
        return candidate, relative_repo_path(repo_root, candidate)

    skills_dir = repo_root / "skills"
    if skills_dir.is_dir():
        if enumerate_skill_directories(skills_dir):
            return skills_dir, "skills"

    if enumerate_skill_directories(repo_root):
        return repo_root, "."

    return None


def enumerate_skill_directories(container_dir: Path) -> list[Path]:
    if not container_dir.exists() or not container_dir.is_dir():
        return []

    candidates = [
        child
        for child in sorted(container_dir.iterdir(), key=lambda item: item.name)
        if child.is_dir() and (child / "SKILL.md").is_file()
    ]
    return candidates


def parse_skill_document(skill_file: Path) -> tuple[dict[str, object], str]:
    raw_content = skill_file.read_text(encoding="utf-8")
    lines = raw_content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must start with YAML frontmatter delimited by '---'.")

    closing_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing_index = index
            break

    if closing_index is None:
        raise ValueError("SKILL.md frontmatter is missing a closing '---' delimiter.")

    frontmatter_text = "".join(lines[1:closing_index])
    body = "".join(lines[closing_index + 1 :]).lstrip("\n")

    try:
        loaded = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML frontmatter: {error}") from error

    if not isinstance(loaded, dict):
        raise ValueError("SKILL.md frontmatter must be a YAML mapping.")

    normalized = {str(key): value for key, value in loaded.items()}
    return normalized, body


def validate_skill_directory(skill_dir: Path) -> tuple[ParsedSkill | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    skill_name = skill_dir.name
    skill_file = skill_dir / "SKILL.md"

    if not skill_file.is_file():
        issues.append(
            ValidationIssue(
                skill_name=skill_name,
                field="SKILL.md",
                rule="Each skill directory must contain a SKILL.md file.",
            )
        )
        return None, issues

    try:
        frontmatter, body = parse_skill_document(skill_file)
    except ValueError as error:
        issues.append(
            ValidationIssue(
                skill_name=skill_name,
                field="frontmatter",
                rule=str(error),
            )
        )
        return None, issues

    name = frontmatter.get("name")
    issues.extend(validate_skill_name(name, skill_name=skill_name, expected_directory=skill_dir.name))

    description = frontmatter.get("description")
    if not isinstance(description, str):
        issues.append(
            ValidationIssue(
                skill_name=skill_name,
                field="description",
                rule="The 'description' field is required and must be a string.",
            )
        )
    else:
        stripped_description = description.strip()
        if not stripped_description:
            issues.append(
                ValidationIssue(
                    skill_name=skill_name,
                    field="description",
                    rule="The 'description' field must not be empty.",
                )
            )
        elif len(stripped_description) > 1024:
            issues.append(
                ValidationIssue(
                    skill_name=skill_name,
                    field="description",
                    rule="The 'description' field must be 1024 characters or fewer.",
                )
            )

    license_value = _validate_optional_string(
        frontmatter.get("license"),
        skill_name=skill_name,
        field="license",
        issues=issues,
    )
    compatibility = _validate_optional_string(
        frontmatter.get("compatibility"),
        skill_name=skill_name,
        field="compatibility",
        issues=issues,
        max_length=500,
    )
    allowed_tools = _validate_optional_string(
        frontmatter.get("allowed-tools"),
        skill_name=skill_name,
        field="allowed-tools",
        issues=issues,
    )

    metadata = frontmatter.get("metadata")
    normalized_metadata: dict[str, str] | None = None
    if metadata is not None:
        if not isinstance(metadata, dict):
            issues.append(
                ValidationIssue(
                    skill_name=skill_name,
                    field="metadata",
                    rule="The 'metadata' field must be a mapping of string keys to string values.",
                )
            )
        else:
            normalized_metadata = {}
            for key, value in metadata.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    issues.append(
                        ValidationIssue(
                            skill_name=skill_name,
                            field="metadata",
                            rule="The 'metadata' field must contain only string keys and string values.",
                        )
                    )
                    normalized_metadata = None
                    break
                normalized_metadata[key] = value

    if issues:
        return None, issues

    return (
        ParsedSkill(
            directory=skill_dir,
            name=str(name).strip(),
            description=str(description).strip(),
            license=license_value,
            compatibility=compatibility,
            allowed_tools=allowed_tools,
            metadata=normalized_metadata,
        ),
        issues,
    )


def validate_skill_name(
    value: object,
    *,
    skill_name: str,
    expected_directory: str | None = None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(value, str):
        issues.append(
            ValidationIssue(
                skill_name=skill_name, field="name", rule="The 'name' field is required and must be a string."
            )
        )
        return issues

    stripped_name = value.strip()
    if not 1 <= len(stripped_name) <= MAX_NAME_LENGTH:
        issues.append(
            ValidationIssue(
                skill_name=skill_name,
                field="name",
                rule="The 'name' field must be between 1 and 64 characters.",
            )
        )
        return issues

    if not NAME_PATTERN.fullmatch(stripped_name):
        issues.append(
            ValidationIssue(
                skill_name=skill_name,
                field="name",
                rule="The 'name' field may only use lowercase letters, digits, and single hyphens.",
            )
        )
        return issues

    if expected_directory is not None and stripped_name != expected_directory:
        issues.append(
            ValidationIssue(
                skill_name=skill_name,
                field="name",
                rule="The 'name' field must match the parent directory name.",
            )
        )

    return issues


def build_skill_markdown(skill_name: str) -> str:
    return (
        f"---\n"
        f"name: {skill_name}\n"
        "description: |\n"
        "  Describe what this skill does and when an agent should use it.\n"
        "---\n\n"
        "## Instructions\n\n"
        "<!-- Add step-by-step instructions here -->\n"
    )


def relative_repo_path(repo_root: Path, target: Path) -> str:
    relative = target.resolve().relative_to(repo_root.resolve())
    relative_text = relative.as_posix()
    return relative_text or "."


def _resolve_repo_path(repo_root: Path, explicit_path: str) -> Path | None:
    raw_path = Path(explicit_path)
    candidate = (repo_root / raw_path).resolve() if not raw_path.is_absolute() else raw_path.resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except ValueError:
        return None
    return candidate


def _validate_optional_string(
    value: object,
    *,
    skill_name: str,
    field: str,
    issues: list[ValidationIssue],
    max_length: int | None = None,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        issues.append(
            ValidationIssue(
                skill_name=skill_name, field=field, rule=f"The '{field}' field must be a string if present."
            )
        )
        return None

    stripped = value.strip()
    if not stripped:
        issues.append(
            ValidationIssue(
                skill_name=skill_name, field=field, rule=f"The '{field}' field must not be empty if present."
            )
        )
        return None

    if max_length is not None and len(stripped) > max_length:
        issues.append(
            ValidationIssue(
                skill_name=skill_name,
                field=field,
                rule=f"The '{field}' field must be {max_length} characters or fewer.",
            )
        )
        return None

    return stripped
