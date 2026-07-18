# trivium

**trivium** is a command-line tool for installing, updating, and managing
[AI agent skills](https://agentskills.io) that follow the Agent Skills
Specification. It handles the lifecycle of skill directories: adding them from
remote repositories, keeping them up to date, and organising them into named
environments, without coupling to a specific agent runtime or framework.

## Why trivium?

Agent skills are self-contained skill trees placed on a filesystem where your
agent runtime can discover them. trivium makes that process reproducible and
safe:

- **Stateless skill trees**: skills are checked out from Git at pinned
  commits. Nothing mutates the skill source after installation.
- **Explicit environment management**: capture the current runtime as a named
  manifest, switch between environments, and keep each one isolated.
- **Minimal surface area**: a single `trv` binary. No daemons or long-running
  processes are required.
- **Safety-first defaults**: credential-bearing URLs are rejected, symlink
  traversal is blocked, untracked skill directories are never silently
  overwritten, and lockfile writes are atomic.

## Quick start

```bash
# Install a repository's skills
trv add https://github.com/example/skills.git --all

# Install specific skills
trv add https://github.com/example/skills.git --skills pdf-processing algorithmic-art

# List what is installed
trv list

# Update everything
trv update

# Capture the current runtime as an environment
trv env create office

# Activate a named environment
trv env activate office
```

## Project status

trivium is in active use by the author. The CLI, lockfile, and environment
formats are evolving. Breaking changes are reflected in the `skills.lock`
format version and documented in release notes.
