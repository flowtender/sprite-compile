# Architecture decision records

One file per decision, named as a lowercase, dashed, present-tense imperative phrase. Each ADR has Status, Context,
Decision and Consequences. A changed decision gets a new ADR that says `Supersedes [old](old.md)` in its Status; the
old one's Status becomes `Superseded by [new](new.md)`.

| ADR | Status |
|---|---|
| [use-one-repository-per-bot](use-one-repository-per-bot.md) | Accepted |
| [run-podman-rootful-with-userns-auto](run-podman-rootful-with-userns-auto.md) | Accepted |
| [link-quadlet-through-one-directory-symlink](link-quadlet-through-one-directory-symlink.md) | Accepted |
| [use-one-podman-network](use-one-podman-network.md) | Accepted |
| [allow-sprites-traffic-through-docker-firewall](allow-sprites-traffic-through-docker-firewall.md) | Accepted |
| [send-ready-after-startup-checks](send-ready-after-startup-checks.md) | Accepted |
| [retry-failed-frontends-in-process](retry-failed-frontends-in-process.md) | Accepted |
| [store-secrets-as-podman-secrets](store-secrets-as-podman-secrets.md) | Accepted |
| [mount-uv-at-bot-install-time](mount-uv-at-bot-install-time.md) | Accepted |
| [edit-messages-via-interaction-token](edit-messages-via-interaction-token.md) | Accepted |
