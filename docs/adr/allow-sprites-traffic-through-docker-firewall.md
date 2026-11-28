# Allow sprites traffic through Docker's firewall

## Status
Accepted 2026-11-27.

## Context
Docker sets the `ip filter` FORWARD chain to policy drop and accepts only its own bridges. A drop in any table wins
over netavark's accept, so containers on `sprites` get DNS (served on the bridge) but no outbound TCP. This applies to
sprite-register host setup, host check and teardown, on hosts with `docker.service`.

## Decision
Host setup installs `sprites-docker-forward.service`, a oneshot unit (`After=`, `PartOf=` and
`WantedBy=docker.service`). Its `ExecStart` inserts two `DOCKER-USER` rules accepting traffic from `sprites0` and
established traffic back to it (checked with `iptables -C` first); its `ExecStop` deletes them. `PartOf=` re-applies
the rules whenever Docker restarts. Host check verifies the rules with `iptables -C`; teardown disables the unit.

Rejected: setting `ip-forward-no-drop` in Docker's `daemon.json`, because it loosens forwarding for the whole host.

## Consequences
Builds on [use-one-podman-network](use-one-podman-network.md): the rules name the `sprites0` bridge.
