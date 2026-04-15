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

# Install to ~/.agents/
trv add <url> -a --global
trv add <url> -a -g
```

**Note:** Options can be placed before or after the URL. All these are valid:
- `trv add https://example.com/repo.git --all`
- `trv add --all https://example.com/repo.git`

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
```

### `info` - Show skill details

```bash
trv info skill-name
```

### `remove` - Remove skills

```bash
trv remove skill1 skill2   # Remove specific skills
trv remove -a              # Remove all skills (--all)
trv remove -a -y           # Skip confirmation (--yes)
```

### `init` - Create a new skill

```bash
trv init my-skill           # Minimal scaffold
trv init my-skill --full    # Include scripts/, references/, assets/
```

## Project vs Global Mode

**Project mode** (default):
- Skills install to `.agents/skills/`
- Metadata stored in `skills.lock`
- If you are in a git repo, the git root is used
- If you are not in a git repo, the current working directory is used

**Global mode** (with `--global`):
- Skills install to `~/.agents/skills/`
- Available across all projects

## What is `skills.lock`?

`skills.lock` records where each skill came from:
- Source repository URL
- Commit hash
- Per-skill content hash
- Skill metadata (description, license, compatibility)

This enables `trv update` to fetch newer versions from the original sources.
