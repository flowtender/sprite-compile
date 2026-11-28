# Send READY after startup checks, with `Notify=true`

## Status
Accepted 2026-11-26.

## Context
`podman-auto-update` rolls back exactly when restarting the unit fails; Podman has no other signal. With the default
(`conmon`) notify mode the unit counts as started as soon as the container runs, so a crash on startup, failed
validation, missing required secret or broken extension would not roll back. This applies to instance quadlets and
`run()` in sprite-compile.

## Decision
Quadlets use `Notify=true` (`--sdnotify=container`). `run()` writes `READY=1` to `$NOTIFY_SOCKET` once the config
validated, the secrets loaded, the bot was built and its extensions loaded, before connecting to Discord.

`Notify=healthy` (5.0+) with a `HealthCmd` is the alternative if an app can't send READY; not needed here.

## Consequences
- An image that crashes on startup, fails validation, lacks a required secret or has a broken extension fails the
  start (at the latest at `TimeoutStartSec`) and is rolled back.
- The config sync/check `ExecStartPre=` runs the new image, so a release whose config fails validation fails the
  start and rolls back too [inferred from systemd semantics; verify].
- Not covered: a release bug that only shows after READY (e.g. a command payload Discord rejects). Its frontend keeps
  retrying ([retry-failed-frontends-in-process](retry-failed-frontends-in-process.md)) and is fixed by pushing a new
  release.
