"""Copied into each sandbox as sitecustomize.py, so Python imports it at startup.

Blocks outbound network connections for everything the tests run. This
enforces the "no network" rule from the prompt instead of just asking for it.
It is defence in depth, not a perfect jail: native code could bypass it.
"""

import socket as _socket

_MESSAGE = "Network access is disabled in the TestSmith sandbox"
_INET = {_socket.AF_INET, getattr(_socket, "AF_INET6", _socket.AF_INET)}
_orig_connect = _socket.socket.connect
_orig_connect_ex = _socket.socket.connect_ex


def _deny(*_args, **_kwargs):
    raise PermissionError(_MESSAGE)


def _connect(self, address):
    if self.family in _INET:
        _deny()
    return _orig_connect(self, address)


def _connect_ex(self, address):
    if self.family in _INET:
        _deny()
    return _orig_connect_ex(self, address)


_socket.socket.connect = _connect
_socket.socket.connect_ex = _connect_ex
_socket.create_connection = _deny
_socket.getaddrinfo = _deny
_socket.gethostbyname = _deny
