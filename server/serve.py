"""管理端启动入口：负责把「监听 ::」变成真正的**双栈**监听。

为什么需要它：uvicorn 的 `--host ::` 会直接让 asyncio 建 socket，而 Windows 的
`IPV6_V6ONLY` 默认是 **1**（v6only）——绑 `::` 只收 IPv6，IPv4 完全不可达：本机
虚拟网卡（如 26.105.249.1）、127.0.0.1 都进不来，启动器会一直停在「启动中…」等
健康检查。Linux 默认是双栈，所以这个问题只在 Windows 显现。

这里在绑 `::` 时显式关掉 v6only，再把 socket 交给 uvicorn；其它地址（具体 IPv4、
具体 IPv6、localhost）保持 uvicorn 原有行为不变。

用法（由 windows\\*.ps1 调用）：
    python -m server.serve --host :: --port 7864 --env-file .env
"""
from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

import uvicorn

# `::` 的各种写法都表示「IPv6 通配」，不能只看字面量
_IPV6_WILDCARD = frozenset(('::', '[::]', '::0'))


def is_ipv6_wildcard(host: str) -> bool:
    """是否 IPv6 通配地址（只有它需要双栈；具体地址绑它本身即可）。"""
    return (host or '').strip().lower() in _IPV6_WILDCARD


def make_dual_stack_socket(port: int) -> socket.socket:
    """绑定 `::` 并同时接受 IPv4 连接（关闭 IPV6_V6ONLY）。

    关闭 v6only 后，同一个 socket 既收 IPv6 也收 IPv4（地址按 v4-mapped 呈现），
    于是 127.0.0.1、局域网 IP、本机虚拟网卡都能访问 —— 与 Linux / Bun 的默认一致。
    """
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'IPV6_V6ONLY'):
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind(('::', port))
    return sock


def main() -> int:
    parser = argparse.ArgumentParser(description='启动 WorkBuddy Manager 管理端')
    # 默认值只作兜底：实际 host/port 由启动脚本从 .env 解析后传入
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=7864)
    parser.add_argument('--env-file', default='.env')
    args = parser.parse_args()

    # 必须先载入 .env，再导入运行配置：否则 PROXY protocol 开关会被冻结为
    # 默认值。Docker 注入环境变量且通常没有 .env，默认缺文件时保持可启动。
    env_path = Path(args.env_file) if args.env_file else None
    if env_path is not None and env_path.is_file():
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
    elif args.env_file and args.env_file != '.env':
        parser.error(f'环境文件不存在：{args.env_file}')

    from . import config as settings
    http = 'server.proxyprotocol:ProxyProtocolH11' if settings.PROXY_PROTOCOL else 'auto'
    # HTTP 转发头只交给 iputil 的可信对端校验。Uvicorn 默认的代理中间件会在
    # 此前重写 scope.client，甚至覆盖 PROXY protocol 已验证的客户端地址。
    server_config = uvicorn.Config(
        'server.main:app', host=args.host, port=args.port,
        proxy_headers=False, http=http,
        # 当前应用没有 WebSocket 路由；禁用它的独立协议切换，避免丢失 TCP
        # 解析器保留的来源和元信息。普通 HTTP / SSE 均保持工作。
        ws='none' if settings.PROXY_PROTOCOL else 'auto',
    )

    if is_ipv6_wildcard(args.host):
        # 自己建 socket 再交给 uvicorn：uvicorn 的 --host 路径无法表达双栈
        sock = make_dual_stack_socket(args.port)
        uvicorn.Server(server_config).run(sockets=[sock])
        return 0

    uvicorn.Server(server_config).run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
