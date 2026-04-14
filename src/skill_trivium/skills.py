import re
import shutil
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import yaml

from skill_trivium.models import ParsedSkill, ValidationIssue

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


def render_skill_document(frontmatter: dict[str, object], body: str) -> str:
    frontmatter_text = yaml.dump(frontmatter, sort_keys=False, allow_unicode=False, default_flow_style=False)
    normalized_body = body.lstrip("\n")
    return f"---\n{frontmatter_text}---\n\n{normalized_body}" if normalized_body else f"---\n{frontmatter_text}---\n"


def write_skill_document(skill_file: Path, frontmatter: dict[str, object], body: str) -> None:
    skill_file.write_text(render_skill_document(frontmatter, body), encoding="utf-8")


def hash_skill_directory(skill_dir: Path, *, skill_document: str | None = None) -> str:
    digest = sha256()
    for path in sorted(skill_dir.rglob("*"), key=lambda item: item.relative_to(skill_dir).as_posix()):
        if path.is_dir():
            continue

        relative_path = path.relative_to(skill_dir).as_posix()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        if relative_path == "SKILL.md" and skill_document is not None:
            digest.update(skill_document.encode("utf-8"))
        else:
            digest.update(path.read_bytes())
        digest.update(b"\0")

    return digest.hexdigest()


def hash_parsed_skill(parsed_skill: ParsedSkill) -> str:
    return hash_skill_directory(
        parsed_skill.directory,
        skill_document=render_skill_document(parsed_skill.frontmatter, parsed_skill.body),
    )


def install_skill_tree(parsed_skill: ParsedSkill, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(parsed_skill.directory, destination)
    write_skill_document(destination / "SKILL.md", parsed_skill.frontmatter, parsed_skill.body)


def repair_installed_skill_if_needed(parsed_skill: ParsedSkill, destination: Path) -> bool:
    if not parsed_skill.warnings:
        return False
    if not destination.is_dir():
        return False
    write_skill_document(destination / "SKILL.md", parsed_skill.frontmatter, parsed_skill.body)
    return True


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

    normalized_frontmatter = dict(frontmatter)

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
        normalized_frontmatter["description"] = stripped_description
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

    # Process allowed-tools to accept a YAML list of strings
    raw_allowed_tools = frontmatter.get("allowed-tools")
    skill_warnings: list[str] = []
    if isinstance(raw_allowed_tools, list):
        if all(isinstance(item, str) for item in raw_allowed_tools):
            normalized = " ".join(item.strip() for item in raw_allowed_tools if item.strip())
            skill_warnings.append(
                f"'allowed-tools' was a YAML list and has been converted to a space-separated string: \"{normalized}\""
            )
            raw_allowed_tools = normalized
            normalized_frontmatter["allowed-tools"] = normalized
        else:
            issues.append(
                ValidationIssue(
                    skill_name=skill_name,
                    field="allowed-tools",
                    rule="The 'allowed-tools' list must contain only strings.",
                )
            )
            raw_allowed_tools = None

    allowed_tools = _validate_optional_string(
        raw_allowed_tools,
        skill_name=skill_name,
        field="allowed-tools",
        issues=issues,
        type_error_msg="The 'allowed-tools' field must be a space-separated string or a list of strings if present.",
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

    normalized_frontmatter["name"] = str(name).strip()
    if license_value is not None:
        normalized_frontmatter["license"] = license_value
    if compatibility is not None:
        normalized_frontmatter["compatibility"] = compatibility
    if allowed_tools is not None:
        normalized_frontmatter["allowed-tools"] = allowed_tools
    if normalized_metadata is not None:
        normalized_frontmatter["metadata"] = normalized_metadata

    return (
        ParsedSkill(
            directory=skill_dir,
            name=str(name).strip(),
            description=str(description).strip(),
            license=license_value,
            compatibility=compatibility,
            allowed_tools=allowed_tools,
            metadata=normalized_metadata,
            frontmatter=normalized_frontmatter,
            body=body,
            warnings=tuple(skill_warnings),
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
    type_error_msg: str | None = None,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        rule_msg = type_error_msg or f"The '{field}' field must be a string if present."
        issues.append(ValidationIssue(skill_name=skill_name, field=field, rule=rule_msg))
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
