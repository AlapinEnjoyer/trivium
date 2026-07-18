# Reference

## Lockfile (`skills.lock`)

The lockfile records the source, commit hash, and metadata for every installed
skill. It is the source of truth for what is installed and where it came from.

```toml
[meta]
version = 1
mode = "project"

[skills]

[skills.pdf-processing]
source_url = "https://github.com/example/skills.git"
skills_path = "skills/pdf-processing"
install_path = ".agents/skills/pdf-processing"
commit_hash = "a1b2c3d4e5f6..."
description = "Extract text from PDF files"
license = "MIT"
```

## Format version

The lockfile format version is validated before any read or write. An
unexpected version causes a hard error rather than silent migration.
When the final installed skill is removed, `skills.lock` is removed as well;
an absent lockfile represents an empty installation.

## Safety guarantees

### Credential rejection

HTTP(S) repository URLs that contain embedded credentials (`user@host`) are
rejected at the CLI layer. Use SSH or a credential helper instead.

### Symlink protection

Skill trees containing symlinks are rejected on install and update. The active
manifest file itself must not be a symlink.

### Path traversal

Skill names containing `..` or `/` are rejected. Installation paths are
relative to the skills directory and cannot escape it.

### Untracked directories

If a skill directory exists on disk but is not recorded in the lockfile,
`trv add` refuses to replace it. Remove or move the directory first.

### Atomic writes

Lockfile writes use `Path.replace()` so the file is never left in a partially
written state.

### `--ignore-validation` behaviour

`--ignore-validation` bypasses Agent Skills Specification validation errors,
such as a missing description or unsupported optional field value. It does not
bypass structural and filesystem safety requirements:

- The repository must contain a parseable `SKILL.md`
- Installation names must be safe relative directory names
- Selected skills may not resolve to the same installation name
- An existing untracked destination is never overwritten

Compatibility normalisation for `metadata` and `allowed-tools` still applies.

## Metadata normalisation

`metadata` is specified as a string-to-string map by the Agent Skills
Specification. Values that are not strings (e.g. `true`, `42`) are converted
to strings on install and reported with a conversion warning.

`allowed-tools` accepts YAML list values. A single-string shorthand is
converted to a one-element list automatically.
