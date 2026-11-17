# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Real loopback QUIC routing/stream controls; no TAK deployment is simulated."""

import asyncio
import contextlib
import datetime
import importlib.util
import io
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

try:
    from aioquic.asyncio import serve
    from aioquic.asyncio.protocol import QuicConnectionProtocol
    from aioquic.quic.configuration import QuicConfiguration
    from aioquic.quic.events import HandshakeCompleted
except ModuleNotFoundError as error:
    if error.name != "aioquic":
        raise
    raise unittest.SkipTest("QUIC tests require tests/requirements-quic.txt") from None
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

SPEC = importlib.util.spec_from_file_location("quic_cot", Path(__file__).with_name("quic-cot.py"))
quic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quic)


class QuicRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=1))
                .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
        (root / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (root / "key.pem").write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.server_config = QuicConfiguration(is_client=False, alpn_protocols=["takstream"])
        self.server_config.load_cert_chain(root / "cert.pem", root / "key.pem")

    async def server(self, **kwargs):
        server = await serve("127.0.0.1", 0, configuration=self.server_config, **kwargs)
        self.addCleanup(server.close)
        return server._transport.get_extra_info("sockname")[1]

    async def test_hostname_selects_connected_loopback_interface_and_exact_peer(self):
        port = await self.server()
        transport, protocol, peer = await quic.open_endpoint("localhost", port, quic.configuration(None, None, None))
        try:
            self.assertEqual(peer, ("127.0.0.1", port))
            self.assertEqual(transport.get_extra_info("peername"), peer)
            self.assertEqual(transport.get_extra_info("sockname")[0], "127.0.0.1")
        finally:
            # No handshake was started by open_endpoint.
            transport.close()

    async def test_peer_route_is_selected_without_forcing_a_loopback_source(self):
        loop = asyncio.get_running_loop()
        transport, protocol = MagicMock(), MagicMock()
        peer = ("10.42.1.7", 8090)
        with patch.object(loop, "getaddrinfo", new=AsyncMock(return_value=[
            (socket.AF_INET, socket.SOCK_DGRAM, 17, "", peer)])), \
                patch.object(loop, "create_datagram_endpoint", new=AsyncMock(
                    return_value=(transport, protocol))) as endpoint:
            self.assertEqual(await quic.open_endpoint("takserver.takserver.svc", 8090,
                                                     quic.configuration(None, None, None)),
                             (transport, protocol, peer))
        self.assertEqual(endpoint.call_args.kwargs, {"remote_addr": peer, "family": socket.AF_INET})

    async def test_actual_quic_stream_carries_cot_on_the_selected_interface(self):
        received = asyncio.get_running_loop().create_future()

        async def answer(reader, writer):
            payload = await reader.read(65536)
            received.set_result(payload)
            writer.write(b"TakProtocolSupport")
            await writer.drain()
            writer.close()

        port = await self.server(stream_handler=lambda reader, writer: asyncio.create_task(answer(reader, writer)))
        transport, protocol, reader, writer = await asyncio.wait_for(
            quic.open_stream("localhost", port, quic.configuration(None, None, None)), 5)
        try:
            payload = quic.cot("routing-fixture").encode()
            writer.write(payload)
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(received, 5), payload)
            self.assertEqual(await asyncio.wait_for(reader.read(65536), 5), b"TakProtocolSupport")
            self.assertEqual(transport.get_extra_info("sockname")[0], "127.0.0.1")
        finally:
            writer.close()
            protocol.close()
            transport.close()

    async def test_refusal_path_reaches_peer_and_requires_a_tls_alert(self):
        class Refuse(QuicConnectionProtocol):
            def quic_event_received(self, event):
                super().quic_event_received(event)
                if isinstance(event, HandshakeCompleted):
                    # Controlled peer closure exercises the client's verdict.
                    # It does not stand in for TAK's certificate authentication.
                    self.close(error_code=0x174, reason_phrase="fixture certificate required")

        port = await self.server(create_protocol=Refuse)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(await asyncio.wait_for(quic.reject("localhost", port), 8), 0)

    async def test_open_peer_without_a_certificate_is_not_counted_as_refusal(self):
        async def answer(reader, writer):
            await reader.read(65536)
            writer.write(b"accepted")
            await writer.drain()
            writer.close()

        port = await self.server(stream_handler=lambda reader, writer: asyncio.create_task(answer(reader, writer)))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(await asyncio.wait_for(quic.reject("localhost", port), 8), 1)

    async def test_failed_or_cancelled_handshake_closes_owned_transport(self):
        for error in (ConnectionError("fixture"), asyncio.CancelledError()):
            for cleanup_failure in (None, RuntimeError("cleanup fixture")):
                transport, protocol = MagicMock(), MagicMock()
                protocol.wait_connected = AsyncMock(side_effect=error)
                protocol.close.side_effect = cleanup_failure
                with self.subTest(error=type(error).__name__, cleanup=cleanup_failure), \
                        patch.object(quic, "open_endpoint", new=AsyncMock(return_value=(
                            transport, protocol, ("127.0.0.1", 8090)))):
                    with self.assertRaises(type(error)) as caught:
                        await quic.open_stream("localhost", 8090, quic.configuration(None, None, None))
                    self.assertIs(caught.exception, error)
                    protocol.close.assert_called_once()
                    transport.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
