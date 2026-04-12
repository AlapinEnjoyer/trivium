# trivium

`trivium` is a Python CLI for installing, updating, inspecting, removing, and scaffolding AI agent skills that follow the [Agent Skills Specification](https://agentskills.io).

It is designed to work well as a local project tool or a user-level global tool, and is compatible with `uvx` / `uv run` workflows.

## Features

- Project mode rooted at the nearest `.git/`
- Global mode rooted at `~/.agents/`
- `skills.lock` lockfile for reproducible skill installs
- Rich terminal output for summaries, warnings, errors, and metadata views
- Validation for skill names, descriptions, metadata, and frontmatter structure
- Conflict handling for same-name skills from different sources
- Local skill scaffolding with `init`

## Requirements

- Python `>=3.13`
- `uv` recommended for running, building, and testing
