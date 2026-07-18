# trivium

Install and manage [Agent Skills](https://agentskills.io) from Git repositories.
`trv` pins skill sources, records reproducible metadata, and lets you switch
between named skill environments.

## Why trivium?

- Git-backed skills pinned to exact revisions
- Project-local or global installations
- Dry runs for add and update operations
- Named environments for repeatable skill sets
- Lockfile validation, atomic writes, and filesystem safety checks

## Install

```bash
uv tool install skill-trivium
```

Or run it without installing:

```bash
uvx --from skill-trivium trv --help
```

## Quick Start

From a Git project, install all skills from a repository:

```bash
trv add https://github.com/example/skills.git --all
```

Inspect and update the installation:

```bash
trv list
trv info skill-name
trv update
```

Capture and switch between skill configurations:

```bash
trv env create focused-work
trv env list
trv env activate focused-work
```

By default, skills are installed under `.agents/skills/` and recorded in
`skills.lock` at the project root. Use `--global` for the user-wide install at
`~/.agents/skills/`.

Remove skills when they are no longer needed:

```bash
trv remove skill-name
trv remove --all --yes
```

## Documentation

Full documentation is available at
[alapinenjoyer.github.io/trivium](https://alapinenjoyer.github.io/trivium/).
It covers installation, commands, environments, the lockfile format, and
safety guarantees.

## Development

```bash
uv sync
make test
```

The project requires Python 3.13 or later. Contributions and bug reports are
welcome on [GitHub](https://github.com/AlapinEnjoyer/trivium).
