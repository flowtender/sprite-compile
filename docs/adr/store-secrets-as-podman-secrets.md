# Store secrets as Podman secrets, mounted as files

## Status
Accepted 2026-11-26.

## Context
Secrets must stay out of the environment, `podman inspect`, Copier answers and git. This applies to secret handling in
sprite-compile and sprite-register.

## Decision
Secrets are stored as Podman secrets named `<instance>.<name>` and mounted read-only at `/run/secrets/<name>`.

## Consequences
Podman's default driver stores secrets unencrypted, root-only, under `/var/lib/containers`.

Later option: sops + age encrypted in git (fits NixOS via sops-nix), feeding the same Podman secrets.
