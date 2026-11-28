# Run Podman rootful with `UserNS=auto`

## Status
Accepted 2026-11-26.

## Context
A container escape lands as whichever host account the container's processes map to. This applies to every instance
quadlet and to host setup.

## Decision
Podman runs rootful, managed with sudo. Each quadlet sets `UserNS=auto`, `ReadOnly=true`, `DropCapability=all` and
`NoNewPrivileges=true`; only the instance's data dir is writable.

`UserNS=auto` gives each container its own unprivileged UID range matching no real account. An escape lands as a
nobody with no home and no sudo, and containers can't read each other's files.

Rejected: rootless Podman under an admin account. An escape would land as a sudoer.

## Consequences
- One image store (`/var/lib/containers`), so base layers are stored once per host.
- Requires Podman >= 5.3.0 (see [link-quadlet-through-one-directory-symlink](link-quadlet-through-one-directory-symlink.md)).
