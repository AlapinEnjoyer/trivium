# trivium

A CLI for installing and updating AI agent skills that follow the [Agent Skills Specification](https://agentskills.io).

`trv` validates skill frontmatter against the spec and applies a small compatibility normalization for common YAML shorthands. In particular, `metadata` is specified as a string-to-string map, so values like `true`, `false`, `null`, or numbers are accepted on install/update, converted to strings, and reported with a conversion warning.

![demo](demo.gif)

## Requirements

- Python >=3.13
- [uv](https://docs.astral.sh/uv/)

## Installation

Install globally with uv:
```bash
uv tool install skill-trivium
```

Or use directly with uvx:
```bash
uvx --from skill-trivium trv
```

Or add an alias for convenience:
```bash
# For bash
echo "alias trv='uvx --from skill-trivium trv'" >> ~/.bashrc
# For zsh
echo "alias trv='uvx --from skill-trivium trv'" >> ~/.zshrc
```

The command is `trv` after tool installation.

## Getting Help

Every command and subcommand in `trv` comes with a built-in help menu. You can append `--help` or `-h` to any command to see the full list of arguments, options, and short flag abbreviations.

```bash
trv --help
trv add -h
trv env --help
```

## Quick Start

```bash
# Install skills from a git repository
trv add https://github.com/example/skills.git --all

# Install specific skills
trv add https://github.com/example/skills.git --skills pdf-processing algorithmic-art

# Capture the current runtime as an environment
trv env create office

# Activate a named environment
trv env activate office

# List installed skills
trv list

# Update installed skills
trv update

```

By default, skill commands use project mode in the current directory. If you are inside a git repository, the project root is the git root. If you are not in a git repository, the project root is simply your current working directory. Use `--global` or `-g` on skill commands when you explicitly want `~/.agents/skills/`.

## Commands

### `add` - Install skills

```bash
# Install all skills from repo (long or short flags)
trv add <url> --all
trv add <url> -a

# Install specific skills
trv add <url> --skills name1 name2
trv add <url> -s name1 name2

# Use explicit skills directory
trv add <url> -a --path skills/
trv add <url> -a -p skills/

# Preview without installing
trv add <url> -a --dry-run
trv add <url> -a -n

# Install despite Agent Skills Specification validation issues
trv add <url> -a --ignore-validation
trv add <url> -a -i

# Replace lockfile-tracked skills from a different source without prompting
trv add <url> -a --yes
trv add <url> -a -y

# Install to ~/.agents/
trv add <url> -a --global
trv add <url> -a -g
```

**Note:** Options can be placed before or after the URL. All these are valid:
- `trv add https://example.com/repo.git --all`
- `trv add --all https://example.com/repo.git`

`--ignore-validation` bypasses Agent Skills Specification validation errors, such as a missing description or unsupported optional field value. It does not bypass structural and filesystem safety requirements: the repository must contain a parseable `SKILL.md`, installation names must be safe relative directory names, selected skills may not resolve to the same installation name, and an existing untracked destination is never overwritten. Compatibility normalization for `metadata` and `allowed-tools` still applies.

### `update` - Update installed skills

```bash
trv update                 # Update all skills
trv update skill1 skill2   # Update specific skills
trv update -n              # Preview without updating (--dry-run)
trv update -g              # Update global skills (--global)
```

### `list` - List installed skills

```bash
trv list          # Show skills table
trv list --json   # Output as JSON
trv list --global # Show global skills
```

### `info` - Show skill details

```bash
trv info skill-name
trv info skill-name --global
```

### `remove` - Remove skills

```bash
trv remove skill1 skill2   # Remove specific skills
trv remove -a              # Remove all skills (--all)
trv remove -a -y           # Skip confirmation (--yes)
trv remove skill1 --global # Remove a global skill
```

### `env` - Manage named environments

```bash
trv env list
trv env list --global
trv env create office
trv env create office --global
trv env activate office
trv env activate office --global
trv env info
trv env info office --global
trv env deactivate
trv env remove office
trv env remove office --global
```

## Project vs Global Mode

**Project mode** (default):
- Skills install to `.agents/skills/`
- Metadata stored in `skills.lock`
- If you are in a git repo, the git root is used
- If you are not in a git repo, the current working directory is used

**Global mode** (with `--global`):
- Skills install to `~/.agents/skills/`
- Metadata is stored in `~/.agents/skills.lock`
- Available across all projects

## What is `skills.lock`?

`skills.lock` records where each skill came from:
- Source repository URL
- Full commit hash
- Per-skill content hash
- Skill metadata (description, license, compatibility)

This enables `trv update` to fetch newer versions from the original sources.

Trivium keeps one runtime lockfile per project and one for the global skill installation. Environment manifests under `.agents/environments/` or `~/.trivium/environments/` contain pinned skill definitions rather than copied skill trees. Lockfile format versions and project/global modes are validated before use. Runtime and environment mutations are serialized, metadata writes are atomic, and staged filesystem changes preserve the previous state when a commit fails.

If a skill directory exists but is not recorded in the corresponding lockfile, `trv add` refuses to replace it. Move or remove that directory explicitly before retrying.

## Environments

Named environments are optional manifests of pinned skills. They do not store copied skill trees; activation checks out the recorded commits and materializes them into the current project's `.agents/skills/` runtime.

- `trv env create <name>` writes a commit-friendly project manifest to `.agents/environments/<name>.lock`
- `trv env create <name> --global` writes a reusable personal manifest to `~/.trivium/environments/<name>.lock`
- `trv env activate <name>` activates only the project manifest with that name
- `trv env activate <name> --global` activates a global manifest into the current project from any directory or repository
- activation is reproducible and never advances pinned commits; use `trv update` to update them intentionally
- `trv add`, `trv update`, and `trv remove` automatically update the active manifest
- global manifest writes reject stale runtimes when another repository changed the same environment
- `trv env deactivate` restores the runtime that preceded activation
- active environments must be deactivated before their manifest can be removed

Project and global environments use separate, explicit namespaces. There is no implicit global fallback or project-local copy of a global manifest.
