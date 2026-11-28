# Link Quadlet through one directory symlink

## Status
Accepted 2026-11-26.

## Context
Instances live in `/srv/sprites/<instance>/`. Quadlet must find their `.container` files without copying them. This
applies to `/etc/containers/systemd/sprites`, `/srv/sprites` and the instance quadlets.

## Decision
`/etc/containers/systemd/sprites` is a symlink to `/srv/sprites` (podman-systemd.unit(5), "Using symbolic links").
Recursion into a symlinked subdirectory needs Podman >= 5.3.0 (containers/podman#23755).

## Consequences
- Absolute paths in `Volume=`, `EnvironmentFile=` etc.: relative paths resolve against the unit's path as quadlet
  sees it, which differs between file and directory symlinks.
- `/srv` must be on the root filesystem (`findmnt /srv` prints nothing): systemd generators run before other
  filesystems are mounted (#25215).
- No drop-ins: the template owns the whole unit file. Drop-ins next to a symlinked unit's target are only found since
  PR #29825 (merged 2026-09).
- `podman quadlet install` (5.6+) is not used: it copies the file, so `/srv` would stop being the single source of
  truth.
- `/usr/lib/podman/quadlet --dryrun` shows what the generator sees.
