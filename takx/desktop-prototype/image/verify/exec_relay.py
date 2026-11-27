#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Reach a TCP port inside a container that has no network, for a browser test.

  exec_relay.py CONTAINER PORT [LISTEN_PORT]

Listens on 127.0.0.1:LISTEN_PORT (default 16080) on the host. Every accepted
connection is piped through 'docker exec -i CONTAINER python3 ...' to
127.0.0.1:PORT inside the container. The container keeps '--network none';
nothing is published and nothing is mounted. Used by browser_probe.js to
load noVNC from the self-test's web container with a real browser. Stops on
SIGTERM or SIGINT.

The bridge wants the signed-in user's token on every request, and a browser
cannot add one to a WebSocket. In a cluster Authservice at the gateway adds
it. Here the relay stands in for Authservice: when the environment variable
TAKX_RELAY_BEARER holds a token, the relay inserts
`Authorization: Bearer <token>` after the request line of every connection
(the bridge serves one request per connection).
"""
import os
import socket
import subprocess
import sys
import threading

INSIDE = r"""
import os, socket, sys, threading
sock = socket.create_connection(("127.0.0.1", int(sys.argv[1])))
def up():
    while True:
        data = os.read(0, 65536)
        if not data:
            break
        sock.sendall(data)
    try:
        sock.shutdown(socket.SHUT_WR)
    except OSError:
        pass
threading.Thread(target=up, daemon=True).start()
while True:
    data = sock.recv(65536)
    if not data:
        break
    os.write(1, data)
"""


def serve(connection, container, port):
    child = subprocess.Popen(["docker", "exec", "-i", container, "python3", "-I", "-c", INSIDE, str(port)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)

    def down():
        try:
            while True:
                data = child.stdout.read(65536)
                if not data:
                    break
                connection.sendall(data)
        except OSError:
            pass
        finally:
            # shutdown, not only close: the other thread is blocked in recv on
            # this socket, and the browser has to see that the bridge ended
            # the connection.
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    threading.Thread(target=down, daemon=True).start()
    token = os.environ.get("TAKX_RELAY_BEARER", "")
    pending = b"" if token else None
    try:
        while True:
            data = connection.recv(65536)
            if not data:
                break
            if pending is not None:
                # Hold the bytes back until the request line is complete.
                pending += data
                line, found, rest = pending.partition(b"\r\n")
                if not found:
                    continue
                data, pending = line + b"\r\nAuthorization: Bearer " + token.encode("ascii") + b"\r\n" + rest, None
            child.stdin.write(data)
    except OSError:
        pass
    finally:
        try:
            child.stdin.close()
        except OSError:
            pass
        child.wait()


def main(argv):
    if len(argv) not in (3, 4):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    container, port = argv[1], int(argv[2])
    listen = int(argv[3]) if len(argv) == 4 else 16080
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", listen))
    server.listen(16)
    print(f"exec_relay: 127.0.0.1:{listen} -> {container} 127.0.0.1:{port}", flush=True)
    try:
        while True:
            connection, _address = server.accept()
            threading.Thread(target=serve, args=(connection, container, port), daemon=True).start()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
