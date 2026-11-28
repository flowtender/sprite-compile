# Use one Podman network, `sprites`, with a pinned bridge and subnet

## Status
Accepted 2026-11-27.

## Context
Podman's default network has DNS off, so containers inherit the host's `127.0.0.53` stub, which they can't reach.
Firewall rules need a fixed bridge name. This applies to `host/sprites.network` and every instance quadlet
(`Network=`).

## Decision
All instances join one Podman network, `sprites` (`host/sprites.network` in sprite-register, installed once per host
by host setup), with DNS on, the bridge pinned to `sprites0` and the subnet to `10.89.0.0/24`.

## Consequences
- Host check refuses the subnet when it overlaps a route the host already has (LAN, VPC, Docker).
- Hosts running Docker need extra firewall rules: see
  [allow-sprites-traffic-through-docker-firewall](allow-sprites-traffic-through-docker-firewall.md).
