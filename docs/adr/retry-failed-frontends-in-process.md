# Retry failed frontends in-process after READY

## Status
Accepted 2026-11-26.

## Context
After READY, failures are transient, not a bad release: the platform being slow or down, an expired token, rate
limits. Gateway drops never reach `run()`, since discord.py reconnects by itself. This applies to `run()` in
sprite-compile and the instance quadlets.

## Decision
`run()` retries a failed frontend's `serve()` on its own (5 s, doubling up to 10 min; back to 5 s after it ran 10
min) while the bot's other frontends keep running. systemd's restart, with its own growing delay
(`Restart=on-failure`, `RestartSec=`, `RestartSteps=`, `RestartMaxDelaySec=`; systemd >= 254), only covers crashes
outside the frontends.

## Consequences
A persistent failure doesn't hit systemd's start limit and leave the unit failed.
