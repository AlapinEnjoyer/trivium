# Usage

## Getting help

Append `--help` or `-h` to any command to see its usage:

```bash
trv --help
trv add -h
trv env --help
```

## Project vs global mode

By default `trv` runs in **project mode**:

- Skills are installed to `.agents/skills/`
- Metadata is stored in `skills.lock` at the project root
- The project root is the nearest Git ancestor (or the current directory)

Use `--global` / `-g` to operate on the machine-wide installation at
`~/.agents/skills/`.

## `add` - Install skills

Options can be placed before or after the URL. Both of these work:

```bash
trv add https://example.com/repo.git --all
trv add --all https://example.com/repo.git
```

```bash
trv add <url> --all
trv add <url> --skills name1 name2
trv add <url> -a -p skills/
trv add <url> -a --dry-run
trv add <url> -a --ignore-validation
trv add <url> -a --yes
trv add <url> -a --global
```

## `list` - List installed skills

```bash
trv list
trv list --json
trv list --global
```

## `info` - Show skill details

```bash
trv info skill-name
trv info skill-name --global
```

## `update` - Update installed skills

```bash
trv update
trv update skill1 skill2
trv update --dry-run
trv update --global
```

## `remove` - Remove skills

```bash
trv remove skill1 skill2
trv remove -a
trv remove -a --yes
trv remove skill1 --global
```

## `env` - Manage environments

```bash
trv env list
trv env create office
trv env activate office
trv env info
trv env deactivate
trv env remove office
```

All `env` subcommands accept `--global` to operate on the global installation.
