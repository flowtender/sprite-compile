# Mount uv at bot install time, not in the base image

## Status
Accepted 2026-11-26.

## Context
Bot Containerfiles need uv to install their dependencies. This applies to the base image and bot Containerfiles.

## Decision
The base image doesn't contain uv. Bot Containerfiles bind-mount it for their install step only, so it never lands in
a layer.

## Consequences
Bot Containerfiles carry the `--mount=from=ghcr.io/astral-sh/uv:<version>` line and pin the uv version themselves.
