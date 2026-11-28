# Use one repository per bot

## Status
Accepted 2026-11-26.

## Context
Bots depend on the sprite-compile library and base image. Changing the library must not force a change to every bot.
This applies to every bot repo, sprite-compile and sprite-register.

## Decision
Each bot lives in its own repository. sprite-compile and sprite-register are separate repositories.

## Consequences
- A bot can stay on an older sprite-compile version without being touched.
- Development spans several repositories; local work uses an editable install of sprite-compile.
