# sprite-compile

Shared core for discord.py bots ("sprites"): config with defaults in code, declared secrets, storage, command and UX
primitives, and a base container image. Design: [docs/architecture.md](docs/architecture.md).

Status: early development. The library, the Discord frontend and the base image exist; the Copier dev template and
sprite-register (deploy tooling) are planned.

## Development

```bash
uv sync
uv run pytest
```
