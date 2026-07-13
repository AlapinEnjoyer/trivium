# trivium

A CLI for installing, updating, and scaffolding AI agent skills that follow the [Agent Skills Specification](https://agentskills.io).

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
trv init --help
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

# Create a new skill scaffold
trv init my-skill
```

By default, `trv` uses project mode in the current directory. If you are inside a git repository, the project root is the git root. If you are not in a git repository, the project root is simply your current working directory. Use `--global` or `-g` when you explicitly want `~/.agents/skills/`.

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

### `init` - Create a new skill

```bash
trv init my-skill           # Minimal scaffold
trv init my-skill --full    # Include scripts/, references/, assets/
trv init my-skill --global  # Scaffold in ~/.agents/skills/
```

### `env` - Manage named environments

```bash
trv env list
trv env list --global
trv env create office
trv env create office --global
trv env create office --shared
trv env create scratch --empty
trv env activate office
trv env activate office --global
trv env info
trv env info office --global
trv env deactivate
trv env deactivate --global
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

Trivium keeps one lockfile per project and one global lockfile. Environment lockfiles under `.agents/environments/` or `~/.trivium/` are separate snapshots, not additional global manifests. Lockfile format versions and project/global modes are validated before use. Writes are atomic, and mutating `add` commands are serialized so concurrent global additions do not overwrite each other's entries.

If a skill directory exists but is not recorded in the corresponding lockfile, `trv add` refuses to replace it. Move or remove that directory explicitly before retrying.

## Environments

Named environments are optional. If you do not use `trv env`, `trv` keeps the current behavior and works directly with the active runtime in `.agents/skills/` and `skills.lock`.

- `trv env create <name>` captures the current runtime by default
- `trv env create <name> --global` captures the current project's runtime and stores the snapshot under `~/.trivium/global/envs/<name>/`
- global environment names share one account-wide namespace; they are not owned by the project that created them
- multiple global environments can coexist when they have different names; creating an existing name fails instead of overwriting it
- remove an existing global environment with `trv env remove <name> --global` before recreating that name
- `--empty` creates an empty environment
- `--shared` also writes a shareable definition to `.agents/environments/<name>.lock`
- `--shared` is project-only and cannot be combined with `--global`
- `trv env activate <name>` swaps the active runtime to that environment
- project activation also falls back to globally stored environments when no project-scoped env with that name exists
- activating a global environment from a project creates a project-local copy; later project changes do not overwrite the stored global snapshot
- `trv env activate <name> --global` activates that environment directly into `~/.agents/skills/`
- if only `.agents/environments/<name>.lock` exists, `trv env activate <name>` materializes a local snapshot from that shared definition first
- `trv env deactivate` restores the previous non-environment runtime
- `trv env remove <name>` removes the local snapshot and shared definition, and auto-deactivates it first if needed

Project-local environment snapshots are stored under a project-specific hashed directory in `~/.trivium/projects/`. Global snapshots are stored under `~/.trivium/global/envs/`. Shared environment definitions are only available in project mode.
