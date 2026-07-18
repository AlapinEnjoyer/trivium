# trivium

Install and manage [Agent Skills](https://agentskills.io) from Git repositories.

`trivium` pins installed skills to exact Git commits, records their state in a
lockfile, and lets you save and switch between named skill environments. It
manages skill directories on disk without coupling your setup to a specific
agent runtime or framework.

![Demo of installing Agent Skills with trivium](demo.gif)

## Why trivium?

* **Pinned Git sources:** Install skills from exact commits for predictable,
  reproducible setups.
* **Lockfile tracking:** Record installed skills and their source metadata in
  `skills.lock`.
* **Project-local or global installs:** Manage skills for one project or for
  your user account.
* **Safe updates:** Preview add and update operations with dry runs.
* **Named environments:** Save and switch between repeatable skill
  configurations.
* **Filesystem safety:** Validate lockfiles, block unsafe paths, and use atomic
  writes.

## Install

Install trivium with [`uv`](https://docs.astral.sh/uv/):

```bash
uv tool install skill-trivium
```

Or run it without installing:

```bash
uvx --from skill-trivium trv --help
```

## Quick start

From the root of your project, install every skill from a Git repository:

```bash
trv add https://github.com/example/skills.git --all
```

Install selected skills:

```bash
trv add https://github.com/example/skills.git \
  --skills pdf-processing algorithmic-art
```

Inspect the current installation:

```bash
trv list
trv info skill-name
```

Preview and apply updates:

```bash
trv update --dry-run
trv update
```

Save and switch between named skill environments:

```bash
trv env create focused-work
trv env list
trv env activate focused-work
```

By default, project-local skills are installed under `.agents/skills/` and
recorded in `skills.lock` at the project root.

Commit `skills.lock` to version control when you want collaborators and CI
environments to reproduce the same skill configuration.

Use `--global` to manage user-wide skills under `~/.agents/skills/`.

Remove individual skills or clear the installation:

```bash
trv remove skill-name
trv remove --all --yes
```

## Documentation

Full documentation is available at
[alapinenjoyer.github.io/trivium](https://alapinenjoyer.github.io/trivium/).

It covers installation, commands, named environments, the lockfile format, and
filesystem safety guarantees.

## Project status

trivium is under active development and is used by its author. The CLI,
lockfile format, and environment format may continue to evolve.

Breaking lockfile changes increment the `skills.lock` format version and are
documented in the release notes.

## Development

```bash
uv sync
make test
```

The project requires Python 3.13 or later.

Contributions and bug reports are welcome through
[GitHub Issues](https://github.com/AlapinEnjoyer/trivium/issues).
