#!/usr/bin/env python3
"""Delivery of ESC/POS payloads to a printer: USB device, TCP or CUPS."""

from __future__ import annotations

import os
import select
import socket
import subprocess
import time

# os.O_NONBLOCK (and select() on the descriptor) only exist on POSIX; on
# Windows a blocking write is the only option, which is fine for files/ports.
NONBLOCK = hasattr(os, "O_NONBLOCK")


def describe(target: str) -> dict:
    kind, _, rest = str(target).partition(":")
    kind = kind.lower()
    info = {"target": target, "kind": kind, "detail": rest}
    if kind in ("device", "file"):
        info["available"] = os.path.exists(rest)
    elif kind in ("socket", "tcp"):
        host, _, port = rest.rpartition(":")
        info["host"], info["port"] = host, int(port or 9100)
        info["available"] = True
    elif kind == "cups":
        try:
            out = subprocess.run(["lpstat", "-p", rest], capture_output=True, timeout=10)
            info["available"] = out.returncode == 0
        except Exception:
            info["available"] = False
    else:
        info["available"] = False
        info["error"] = "unknown target scheme"
    return info


def _write_fd(fd, payload, timeout):
    view = memoryview(payload)
    deadline = time.time() + timeout
    while view:
        try:
            written = os.write(fd, view)
            view = view[written:]
        except BlockingIOError:
            if not NONBLOCK:
                raise
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("timed out writing to the printer")
            select.select([], [fd], [], min(1.0, remaining))
        except InterruptedError:
            continue


def send(payload: bytes, target: str, timeout: float = 20.0) -> tuple:
    """Send `payload` to `target`.  Returns ``(ok, message)``."""
    kind, _, rest = str(target).partition(":")
    kind = kind.lower()
    try:
        if kind in ("device", "file"):
            if not os.path.exists(rest):
                return False, "%s does not exist" % rest
            fd = os.open(rest, os.O_WRONLY | (os.O_NONBLOCK if NONBLOCK else 0))
            try:
                _write_fd(fd, payload, timeout)
                time.sleep(0.4)
            finally:
                os.close(fd)
            return True, "wrote %d bytes to %s" % (len(payload), rest)

        if kind in ("socket", "tcp"):
            host, _, port = rest.rpartition(":")
            connection = socket.create_connection((host, int(port or 9100)), timeout=timeout)
            try:
                connection.sendall(payload)
                connection.shutdown(socket.SHUT_WR)
            finally:
                connection.close()
            return True, "sent %d bytes to %s:%s" % (len(payload), host, port)

        if kind == "cups":
            result = subprocess.run(["lp", "-d", rest, "-o", "raw", "-"],
                                    input=payload, capture_output=True, timeout=timeout + 20)
            if result.returncode != 0:
                return False, result.stderr.decode("utf-8", "replace").strip()
            return True, result.stdout.decode("utf-8", "replace").strip() or "queued in CUPS"

        return False, "unknown target scheme %r" % kind
    except Exception as exc:  # noqa: BLE001 - reported to the caller
        return False, "%s: %s" % (type(exc).__name__, exc)
