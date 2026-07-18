# Installation

## Requirements

- Python 3.13 or later
- [uv](https://docs.astral.sh/uv/)

## Install globally

```bash
uv tool install skill-trivium
```

## Run without installing

```bash
uvx --from skill-trivium trv
```

## CLI alias

```bash
echo "alias trv='uvx --from skill-trivium trv'" >> ~/.zshrc
```

After installation the command is `trv`.

## Operating systems

trivium uses native filesystem paths and supports POSIX systems and Windows.
Project installations are stored in `<project>/.agents/skills/`. Global
installations are stored in `~/.agents/skills/`, which resolves to the current
user's home directory on each operating system.
