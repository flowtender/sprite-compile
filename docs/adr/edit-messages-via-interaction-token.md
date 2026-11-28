# Edit earlier bot messages through a stored interaction token first

## Status
Accepted 2026-11-26.

## Context
An interaction token edits a message for 15 minutes after its interaction, also where the bot can't see the channel.
Tested 2026-11-20: an expired token can't edit (401, error 50027). This applies to `MessageHandles` in
`sprite_compile.discord`.

## Decision
The token is stored per message in the cache database (`MessageHandles`), so it works across a restart. When it has
expired, editing falls back to `get_partial_message` with the bot token.
