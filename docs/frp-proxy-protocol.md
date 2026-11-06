# FRP 穿透与真实客户端 IP

管理端支持在同一个 HTTP 端口接收 PROXY protocol v1 和 v2，默认关闭。启用后，来自指定可信 TCP 对端的 PROXY 前缀会在 HTTP 解析前被读取，传入的源地址用于请求日志、登录限流、Key IP 白名单和 IP 数量限制。已验证的 PROXY 地址优先于客户端提交的 HTTP 转发头。

普通 HTTP 连接仍直接处理，因此容器健康检查和直接访问面板可继续使用。协议头只在每条 TCP 连接开始解析一次；格式错误、超长或来自非可信对端的 PROXY 前缀会关闭连接，不会回退为可伪造的 HTTP 头。

v1 头最大 108 字节，v2 头（包括附加 TLV）最大 4096 字节。首个协议前缀默认等待 5 秒，可用 `WB_PROXY_PROTOCOL_TIMEOUT_SECONDS` 设置为 1–30 秒；确认进入普通 HTTP 或读取完 PROXY 头后，此计时器停止，不限制后续 HTTP 上传和流式响应的时长。

## 管理端配置

容器部署在实际使用的 Compose 文件中给管理端添加环境变量：

```yaml
services:
  workbuddy-manager:
    environment:
      WB_PROXY_PROTOCOL: "1"
      WB_PROXY_PROTOCOL_TRUSTED_CIDRS: "127.0.0.0/8,::1/128,172.24.0.1/32"
```

`172.24.0.1/32` 只是示例，必须换成管理端实际看到的 frpc TCP 对端。frpc 运行在宿主机、连接 Docker 发布端口时，容器通常看到该 Docker 网络的宿主机网关；通过 `docker inspect workbuddy-manager` 的 `NetworkSettings.Networks.*.Gateway` 查看网关。frpc 与管理端都原生运行时通常只需回环地址；frpc 位于另一个容器或服务器时填写该对端的固定 IP。

可信对端独立于 HTTP 转发头设置 `WB_TRUST_PROXY` / `WB_TRUSTED_PROXY_CIDRS`，FRP 不需要开启 HTTP 转发头信任。只添加具体可信代理地址，不要填写整个 Docker、局域网或公网网段。

仓库自带的 `docker-compose.yml` 已透传协议开关、可信网段和等待秒数，可在同目录 `.env` 设置它们；外层自定义 Compose 需要自行添加。保持现有端口映射、数据卷和附加组，重建管理端后再启用 frpc：

```bash
docker compose build workbuddy-manager
docker compose up -d --no-deps --force-recreate workbuddy-manager
```

原生部署将环境变量写入安装目录 `.env` 并重启服务。统一启动入口为：

```bash
python -m server.serve --host 127.0.0.1 --port 7864 --env-file .env
```

镜像、systemd 模板和 Windows 脚本都使用这个入口。仅运行 `python -m uvicorn server.main:app` 不会解析 PROXY protocol；旧 systemd 部署升级代码后也应同步修改服务的 `ExecStart`。

## frpc 配置

在已有的面板 TCP 代理中增加一行，保留名称、认证、服务端地址和端口：

```toml
[[proxies]]
name = "wb-proxy-tcp"
type = "tcp"
localIP = "127.0.0.1"
localPort = 8790
remotePort = 8790
transport.proxyProtocolVersion = "v2"
```

这里的 `8790` 对应已修改的面板宿主机端口；容器内部仍是 `7864`。也支持将版本设为 `"v1"`。先部署支持该协议的管理端，再用当前 FRP 的进程管理方式校验、重载或重启这份 frpc 配置；不要开启后把协议前缀发给旧版 HTTP 服务，也不要修改其它转发。

PROXY protocol 本身不提供认证或加密，可信 TCP 对端限制负责防止其它来源冒充真实 IP。如果 frps 前面还存在 TCP 负载均衡，传入的地址取决于 frps 看到的来源；需要在那一层也正确传递地址。通过 HTTP 反向代理后再到 frps 时，PROXY 传入的是该 HTTP 代理的地址。

## 回退

回退至不支持该协议的管理端之前，先删除 frpc 的 `transport.proxyProtocolVersion` 并重载，再回退管理端镜像。只关闭管理端解析、保留 frpc 的 PROXY 前缀，会导致穿透访问失败。
