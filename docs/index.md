# trivium

**trivium** is a command-line tool for reproducibly installing, updating, and
managing [Agent Skills](https://agentskills.io) stored in Git repositories.

It pins installed skills to exact Git commits, records their state in a
lockfile, and lets you save and switch between named skill environments.
Trivium is independent of any particular agent runtime or framework. It manages
the skill directories on disk and leaves discovery and execution to your agent.

## Why trivium?

Agent Skills are directories placed on the filesystem where a compatible agent
can discover them. Copying or cloning those directories manually works, but it
can make installations difficult to reproduce, inspect, and update safely.

Trivium provides:

* **Pinned Git sources:** Each installed skill is tied to an exact commit, so
  the source does not change unexpectedly after installation.
* **Reproducible installations:** Installed skills and source metadata are
  recorded in `skills.lock`.
* **Named environments:** Capture a skill configuration, keep configurations
  isolated, and switch between them when your workflow changes.
* **Agent independence:** Manage skills without coupling your setup to a
  particular agent runtime or framework.
* **Minimal operation:** Use a single `trv` command with no daemon, service, or
  long-running process.
* **Safety-first defaults:** Credential-bearing URLs are rejected, symlink
  traversal is blocked, untracked skill directories are not silently
  overwritten, and lockfile writes are atomic.

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

```bash
# Install every skill from a repository
trv add https://github.com/example/skills.git --all

# Install selected skills
trv add https://github.com/example/skills.git \
  --skills pdf-processing algorithmic-art

# Inspect the current installation
trv list
trv info pdf-processing

# Preview and apply updates
trv update --dry-run
trv update

# Save the current skill configuration
trv env create office

# List and activate named environments
trv env list
trv env activate office
```

By default, project-local skills are installed under `.agents/skills/` and
recorded in `skills.lock` at the project root. Commit the lockfile to version
control when you want collaborators and CI environments to reproduce the same
skill configuration.

Use `--global` to manage user-wide skills under `~/.agents/skills/`.

## Project status

trivium is under active development and is used by its author. The CLI,
lockfile format, and environment format may continue to evolve.

Breaking lockfile changes increment the `skills.lock` format version and are
documented in the release notes.
