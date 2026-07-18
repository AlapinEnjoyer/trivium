# Environments

Named environments let you capture and switch between different skill
configurations without losing the ability to update individual skills.

## How they work

An environment is a **manifest file** that records which skills are installed
and at which commit. It does **not** store a copy of the skill trees.

```
.agents/environments/
├── office.lock   # project-scoped manifest
├── home.lock
└── travel.lock
```

Activating an environment checks out the recorded commits into the active
runtime tree. Deactivating restores the runtime that preceded activation.

## Create an environment

```bash
trv env create office
```

This captures the current `.agents/skills/` state and writes a manifest to
`.agents/environments/office.lock`.

Global environments (`trv env create office --global`) are stored under
`~/.trivium/environments/` and can be activated from any project.

## Activate an environment

```bash
trv env activate office
trv env activate office --global
```

Activation is reproducible and never advances a pinned commit. Use
`trv update` inside an active environment to intentionally update pinned
revisions.

## Automatic manifest updates

While an environment is active, `trv add`, `trv update`, and `trv remove`
automatically update the active manifest to match the new runtime state.

## Global manifest locking

Global manifest mutations use a filesystem lock so concurrent operations wait
for one another instead of writing at the same time. The lock coordinates
access to shared global environment metadata; it is not part of the manifest
contents.

## Namespaces

Project and global environments use separate, explicit namespaces. There is no
implicit fallback from project to global, and activation never creates a
project-local copy of a global manifest.
