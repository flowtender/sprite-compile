"""The bits of systemd's protocol a sprite needs: knowing it runs under systemd, and the READY notification."""

from __future__ import annotations

import os
import socket

NOTIFY_SOCKET_ENV = "NOTIFY_SOCKET"


def under_systemd() -> bool:
    """True in a systemd service (systemd sets `$INVOCATION_ID` for every unit it starts)."""
    return bool(os.environ.get("INVOCATION_ID"))


def notify_ready() -> None:
    """Send `READY=1` to `$NOTIFY_SOCKET` (sd_notify(3)); does nothing when it isn't set. With `Notify=true`, the
    quadlet's start only succeeds once this arrives, so a release that fails before it is rolled back."""
    address = os.environ.get(NOTIFY_SOCKET_ENV)
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]  # abstract socket namespace
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify_socket:
        notify_socket.connect(address)
        notify_socket.sendall(b"READY=1")
