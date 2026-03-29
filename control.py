"""
FILE: control.py
Purpose: Implements the private Unix socket control channel used by the menu bar
app and CLI helpers to talk to the daemon.
Depends on: app_paths.py for socket resolution plus Python stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import socket
import threading
from typing import Callable, Dict

from app_paths import AppPaths, ensure_runtime_directories


ControlHandler = Callable[[Dict[str, object]], Dict[str, object]]


@dataclass
class ControlServer:
    """Small JSON-over-Unix-socket server for private local daemon control."""

    paths: AppPaths
    logger: logging.Logger
    handler: ControlHandler

    def __post_init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._serve_loop, name="control-server", daemon=True)
        self._server_socket: socket.socket | None = None

    def start(self) -> None:
        """Starts the background accept loop."""

        self._thread.start()

    def stop(self) -> None:
        """Stops the accept loop and cleans up the Unix socket file."""

        self._stop_event.set()
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except OSError:
                pass
        self._thread.join(timeout=2.0)
        try:
            self.paths.socket_path.unlink()
        except FileNotFoundError:
            pass

    def _serve_loop(self) -> None:
        """Accepts JSON requests until the daemon shuts down."""

        ensure_runtime_directories(self.paths)
        try:
            self.paths.socket_path.unlink()
        except FileNotFoundError:
            pass

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket = server
        server.bind(str(self.paths.socket_path))
        self.paths.socket_path.chmod(0o600)
        server.listen()
        server.settimeout(0.5)

        while not self._stop_event.is_set():
            try:
                connection, _address = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            with connection:
                try:
                    payload = connection.recv(65_536)
                    request = json.loads(payload.decode("utf-8"))
                    response = self.handler(request)
                except Exception as exc:  # pragma: no cover - defensive production path
                    self.logger.exception("Control command failed")
                    response = {"ok": False, "error": str(exc)}

                response_bytes = (json.dumps(response) + "\n").encode("utf-8")
                connection.sendall(response_bytes)


def send_control_command(
    paths: AppPaths,
    command: str,
    payload: Dict[str, object] | None = None,
    timeout: float = 2.0,
) -> Dict[str, object]:
    """Sends one control command to the daemon and returns the parsed JSON response."""

    message = {"command": command}
    if payload:
        message.update(payload)

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    with client:
        client.connect(str(paths.socket_path))
        client.sendall(json.dumps(message).encode("utf-8"))
        response = client.recv(65_536)
    return json.loads(response.decode("utf-8"))
