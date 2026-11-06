"""在 HTTP 解析之前接收可信 FRP 的 PROXY protocol v1 / v2。"""
from __future__ import annotations

import asyncio
import ipaddress
import struct
from dataclasses import dataclass
from typing import Any

from uvicorn.protocols.http.h11_impl import H11Protocol

from . import config

_V1_PREFIX = b'PROXY '
_V2_PREFIX = b'\r\n\r\n\x00\r\nQUIT\n'
_V1_MAX_BYTES = 108
_SCOPE_EXTENSION = 'workbuddy.proxy_protocol'


def _normalized_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    addr = ipaddress.ip_address(value)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _trusted_networks() -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks = []
    for value in config.PROXY_PROTOCOL_TRUSTED_CIDRS:
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise ValueError('WB_PROXY_PROTOCOL_TRUSTED_CIDRS contains an invalid CIDR') from exc
        if network.prefixlen == 0:
            raise ValueError('WB_PROXY_PROTOCOL_TRUSTED_CIDRS must not trust every address')
        networks.append(network)
    return tuple(networks)


_TRUSTED_NETWORKS = _trusted_networks()


@dataclass(frozen=True)
class _ProxyInfo:
    version: int
    command: str
    source: tuple[str, int] | None
    destination: tuple[str, int] | None


class _ProxySourceApp:
    """每个连接独立的元信息，使 keep-alive 请求都保留已验证的来源。"""

    def __init__(self, app: Any, info: _ProxyInfo, peer: tuple[str, int] | None) -> None:
        self.app = app
        self.info = info
        self.peer = peer

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        # 此字段只由 TCP 解析器创建；客户端的 HTTP 头无法写入 ASGI extensions。
        scope.setdefault('extensions', {})[_SCOPE_EXTENSION] = {
            'version': self.info.version,
            'command': self.info.command,
            'source': self.info.source or self.peer,
            'destination': self.info.destination,
            'peer': self.peer,
        }
        await self.app(scope, receive, send)


def _parse_v1(header: bytes) -> _ProxyInfo:
    try:
        fields = header[:-2].decode('ascii').split(' ')
    except UnicodeDecodeError as exc:
        raise ValueError('non-ASCII v1 header') from exc
    if len(fields) >= 2 and fields[:2] == ['PROXY', 'UNKNOWN']:
        # UNKNOWN 允许附加信息，协议规定接收端全部忽略。
        return _ProxyInfo(1, 'UNKNOWN', None, None)
    if len(fields) != 6 or fields[0] != 'PROXY' or fields[1] not in ('TCP4', 'TCP6'):
        raise ValueError('unsupported v1 address family')
    family = 4 if fields[1] == 'TCP4' else 6
    if '%' in fields[2] or '%' in fields[3]:
        raise ValueError('scoped v1 address is not supported')
    try:
        source = ipaddress.ip_address(fields[2])
        destination = ipaddress.ip_address(fields[3])
    except ValueError as exc:
        raise ValueError('invalid v1 address') from exc
    if source.version != family or destination.version != family:
        raise ValueError('v1 address family mismatch')
    if any(not value.isascii() or not value.isdigit() for value in fields[4:]):
        raise ValueError('invalid v1 port')
    source_port, destination_port = map(int, fields[4:])
    if not (0 <= source_port <= 65535 and 0 <= destination_port <= 65535):
        raise ValueError('invalid v1 port')
    return _ProxyInfo(
        1, 'PROXY',
        (str(_normalized_address(str(source))), source_port),
        (str(_normalized_address(str(destination))), destination_port),
    )


def _parse_v2(header: bytes) -> _ProxyInfo:
    version_command, family_protocol = header[12:14]
    if version_command >> 4 != 2:
        raise ValueError('unsupported v2 version')
    command = version_command & 0x0f
    if command == 0:
        # LOCAL 的地址族、协议和附加数据按规范全部忽略，保留真实 TCP 对端。
        return _ProxyInfo(2, 'LOCAL', None, None)
    if command != 1:
        raise ValueError('unsupported v2 command')
    if family_protocol == 0x11:  # AF_INET / STREAM
        address_bytes, address_type = 12, ipaddress.IPv4Address
        width = 4
    elif family_protocol == 0x21:  # AF_INET6 / STREAM
        address_bytes, address_type = 36, ipaddress.IPv6Address
        width = 16
    else:
        raise ValueError('unsupported v2 address family or transport')
    payload = header[16:]
    if len(payload) < address_bytes:
        raise ValueError('truncated v2 address block')
    source = address_type(payload[:width])
    destination = address_type(payload[width:width * 2])
    source_port, destination_port = struct.unpack('!HH', payload[width * 2:address_bytes])
    # 不使用 TLV 提供的地址或 TLS 信息。额外 TLV 仍需有完整的 type/length/value，
    # 防止损坏的头静默被当作有效的客户端地址。
    position = address_bytes
    while position < len(payload):
        if len(payload) - position < 3:
            raise ValueError('truncated v2 TLV')
        length = struct.unpack('!H', payload[position + 1:position + 3])[0]
        position += 3 + length
        if position > len(payload):
            raise ValueError('truncated v2 TLV')
    return _ProxyInfo(
        2, 'PROXY',
        (str(_normalized_address(str(source))), source_port),
        (str(_normalized_address(str(destination))), destination_port),
    )


class ProxyProtocolH11(H11Protocol):
    """可选 PROXY 头的 H11 监听器，同时接受正常 HTTP / 健康探针。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._proxy_buffer = bytearray()
        self._proxy_decided = False
        self._proxy_failed = False
        self._proxy_peer: tuple[str, int] | None = None
        self._proxy_peer_trusted = False
        self._proxy_timeout: asyncio.TimerHandle | None = None

    def connection_made(self, transport: asyncio.Transport) -> None:
        super().connection_made(transport)
        if self.client is not None:
            try:
                addr = _normalized_address(self.client[0])
                self._proxy_peer = (str(addr), self.client[1])
                self.client = self._proxy_peer
                self._proxy_peer_trusted = any(addr in network for network in _TRUSTED_NETWORKS)
            except ValueError:
                self._proxy_peer = self.client
        self._proxy_timeout = self.loop.call_later(
            config.PROXY_PROTOCOL_TIMEOUT_SECONDS, self._prefix_timeout,
        )

    def _cancel_prefix_timeout(self) -> None:
        if self._proxy_timeout is not None:
            self._proxy_timeout.cancel()
            self._proxy_timeout = None

    def _prefix_timeout(self) -> None:
        self._proxy_timeout = None
        if not self._proxy_decided:
            self._reject('initial header timeout')

    def _reject(self, reason: str) -> None:
        self._proxy_failed = True
        self._cancel_prefix_timeout()
        self._proxy_buffer.clear()
        self.logger.warning('Rejected PROXY protocol connection from %s: %s', self._proxy_peer, reason)
        self.transport.close()

    def _accept_http(self, data: bytes, info: _ProxyInfo | None = None) -> None:
        self._proxy_decided = True
        self._cancel_prefix_timeout()
        self._proxy_buffer.clear()
        if info is not None:
            if info.source is not None:
                self.client = info.source
            self.app = _ProxySourceApp(self.app, info, self._proxy_peer)
        if data:
            super().data_received(data)

    def data_received(self, data: bytes) -> None:
        if self._proxy_failed:
            return
        if self._proxy_decided:
            super().data_received(data)
            return
        # 只存储有限的头前缀；大请求的 HTTP body 留在当前回调的数据中。
        take = min(len(data), config.PROXY_PROTOCOL_MAX_HEADER_BYTES - len(self._proxy_buffer))
        self._proxy_buffer.extend(data[:take])
        buffered = bytes(self._proxy_buffer)
        remainder = data[take:]
        v1 = buffered.startswith(_V1_PREFIX)
        v2 = buffered.startswith(_V2_PREFIX)
        if not v1 and not v2:
            if _V1_PREFIX.startswith(buffered) or _V2_PREFIX.startswith(buffered):
                return
            self._accept_http(buffered + remainder)
            return
        if not self._proxy_peer_trusted:
            self._reject('untrusted TCP peer')
            return
        try:
            if v1:
                end = buffered.find(b'\r\n')
                if end < 0:
                    if len(buffered) >= _V1_MAX_BYTES:
                        raise ValueError('v1 header too long')
                    return
                size = end + 2
                if size > _V1_MAX_BYTES:
                    raise ValueError('v1 header too long')
                info = _parse_v1(buffered[:size])
            else:
                if len(buffered) < 16:
                    return
                size = 16 + struct.unpack('!H', buffered[14:16])[0]
                if size > config.PROXY_PROTOCOL_MAX_HEADER_BYTES:
                    raise ValueError('v2 header too long')
                if len(buffered) < size:
                    return
                info = _parse_v2(buffered[:size])
        except ValueError as exc:
            self._reject(str(exc))
            return
        self._accept_http(buffered[size:] + remainder, info)

    def connection_lost(self, exc: Exception | None) -> None:
        self._cancel_prefix_timeout()
        self._proxy_buffer.clear()
        super().connection_lost(exc)
