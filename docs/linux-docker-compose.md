# Linux Docker Compose 部署指南（本仓库定制版）

适用仓库：<https://github.com/Wenzixi2010/workbuddy-manager>

本文部署包含以下功能的版本：账号 JSON 导入、旧版 SQLite 迁移、账号与 Key 双向权限、Key 再次查看、创建自定义 Key。

> **重要**
>
> - 使用本仓库源码构建，不要直接使用官方 `latest` 镜像，否则可能没有上述定制功能。
> - 本系统由 Python 管理端和 **Go 版 workbuddy2api 上游**组成。旧的 Python 版 workbuddy2api 可以作为数据迁移来源，不应直接替代这里的 Go 上游。
> - 账号隔离要求同时部署 `0051-account-key-scope.patch`。仅更新管理端不够。
> - 以下是**新部署**流程。若目录已有账号、数据库或配置，先备份，不要覆盖初始化。
> - 不要将账号凭据、数据库、配置中的 Key 或备份上传到 GitHub。

## 1. 环境要求

建议使用 Ubuntu 22.04/24.04 或 Debian 12，预留足够内存和磁盘用于 Node、Go 镜像构建；低内存服务器可能需要增加 swap。架构取决于基础镜像支持，通常使用 amd64 或 arm64。

先安装 Docker Engine、Buildx 与 Docker Compose 插件，参考对应系统的官方指南：

- Ubuntu：<https://docs.docker.com/engine/install/ubuntu/>
- Debian：<https://docs.docker.com/engine/install/debian/>

验证：

```bash
sudo docker version
sudo docker buildx version
sudo docker compose version
```

本文其余 **Linux 命令统一在 root 的 Bash 会话中执行**，以避免 `/opt` 和容器卷权限混乱。先进入：

```bash
sudo -i
bash
set -euo pipefail
apt-get update
apt-get install -y git curl python3 ca-certificates
```

`set -e` 会让失败的部署命令停止，不应跳过错误继续执行。退出 root 会话后再次操作时，需要重新进入并切换到部署目录。

## 2. 从你的 GitHub 仓库克隆管理端

```bash
mkdir -p /opt/workbuddy-stack
cd /opt/workbuddy-stack

git clone --branch main --single-branch \
  https://github.com/Wenzixi2010/workbuddy-manager.git manager

mkdir upstream
test -f manager/dev/upstream-patches/0051-account-key-scope.patch
```

如果仓库设为私有，请使用你自己的 GitHub SSH 或凭据管理方式认证；不要把 Token 写进克隆 URL、Compose 文件或文档。

预期目录结构：

```text
/opt/workbuddy-stack/
├── compose.yml                  # 下一步创建的整套服务配置
├── manager/                     # 从你的仓库克隆
│   ├── server/
│   ├── web/
│   ├── deploy/
│   ├── dev/upstream-patches/
│   └── data/                    # 管理员、Key、数据库等持久化数据
└── upstream/                    # Go 上游
    ├── Dockerfile
    ├── config.json              # 本机生成，不上传仓库
    ├── auths/                   # 账号凭据
    └── data/                    # 上游状态
```

从仓库克隆已经包含 Dockerfile 所需的 `deploy/` 等目录。不需要在 Linux 宿主机安装 Node 或 Go，它们在 Docker 构建阶段运行。

## 3. 准备 Go 上游源码并应用补丁

本仓库采用独立上游源码资产，再应用管理端仓库中的补丁。**不要从旧 Python 项目复制代码到这个目录。**

```bash
cd /opt/workbuddy-stack

curl -fL --retry 3 \
  https://github.com/ithtelab/workbuddy-manager/releases/download/upstream-src/workbuddy2api-src.tar.gz \
  -o /tmp/workbuddy-upstream-src.tar.gz

# 留下本次源码包摘要，便于后续核对；这不是独立的签名验证。
sha256sum /tmp/workbuddy-upstream-src.tar.gz > upstream-source.sha256

tar -xzf /tmp/workbuddy-upstream-src.tar.gz \
  -C /opt/workbuddy-stack/upstream --strip-components=1

python3 manager/dev/apply_upstream_patches.py \
  /opt/workbuddy-stack/upstream
```

补丁脚本应成功结束，最后包含：

```text
applied: 0051-account-key-scope.patch
```

如果资产已经包含该补丁，出现 `already applied` 也正常。

> 上游源码资产可能更新。若脚本提示补丁不匹配，**不要跳过校验、不要继续部署未打补丁的上游**。需要使用兼容的源码快照或调整并验证补丁。

## 4. 初始化配置与数据目录

```bash
cd /opt/workbuddy-stack
mkdir -p manager/data upstream/auths upstream/data

python3 - <<'PY'
import json
import secrets
from pathlib import Path

root = Path('/opt/workbuddy-stack/upstream')
target = root / 'config.json'
if target.exists():
    raise SystemExit('config.json 已存在，未覆盖；请检查现有配置。')

config = json.loads((root / 'config.example.json').read_text(encoding='utf-8'))
config['listen'] = ':7863'
config['api_key'] = secrets.token_urlsafe(48)
config['auth_dir'] = '/opt/workbuddy2api/auths'
config['state_file'] = '/opt/workbuddy2api/data/state.json'
config.setdefault('admin', {})['enabled'] = True

# 首次部署不默认运行自动任务，之后按需开启。
for name in config.get('schedule', {}):
    if name.endswith('_enabled'):
        config['schedule'][name] = False

target.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
target.chmod(0o600)
print('上游配置已生成；内部 Key 不需要发给客户端。')
PY

chown -R 10001:10001 manager/data upstream
chmod 700 manager/data upstream/auths upstream/data
```

两个镜像均使用 UID `10001`，需要一致的目录读写权限。不设置时可能出现 `unable to open database file`、账号读不到或导入失败。这里使用本机磁盘 bind mount；NFS/SMB、rootless Docker 或 SELinux 主机还需按其权限机制调整，不能用 `chmod 777` 代替。

**区分两种 Key：**

- `upstream/config.json` 的 `api_key`：管理端访问上游的内部凭据，管理端直接读取共享配置。不要发给下游用户。
- 面板「密钥」页创建的 Key：供客户端调用，支持自定义、额度和账号权限限制。

## 5. 创建 Compose 文件

不要直接运行管理端仓库根目录那份单服务 Compose；在**外层目录**创建下面的双服务配置。

```bash
cd /opt/workbuddy-stack

cat > compose.yml <<'YAML'
services:
  workbuddy2api:
    build:
      context: ./upstream
    image: workbuddy2api:custom
    container_name: workbuddy2api
    restart: unless-stopped
    environment:
      TZ: Asia/Shanghai
    working_dir: /opt/workbuddy2api
    entrypoint:
      - /app/wb2api
      - -config
      - /opt/workbuddy2api/config.json
    volumes:
      - ./upstream:/opt/workbuddy2api
    expose:
      - "7863"

  workbuddy-manager:
    build:
      context: ./manager
    image: workbuddy-manager:custom
    container_name: workbuddy-manager
    restart: unless-stopped
    depends_on:
      - workbuddy2api
    environment:
      TZ: Asia/Shanghai
      WB2API_BASE: http://workbuddy2api:7863
      WB2API_CONTAINER: workbuddy2api
      WB2API_MODE: docker
      WB_AUTH_DIR: /opt/workbuddy2api/auths
      WB_UPSTREAM_CONFIG: /opt/workbuddy2api/config.json
      WB_UPSTREAM_DIR: /opt/workbuddy2api
      WB_DATA_DIR: /app/data
      WB_TRUST_PROXY: "0"
      WB_ENABLE_DOCS: "0"
    ports:
      - "127.0.0.1:7864:7864"
    volumes:
      - ./manager/data:/app/data
      - ./upstream:/opt/workbuddy2api
YAML

docker compose config --quiet
docker compose up -d --build
```

说明：

- 上游的 `7863` 不映射到宿主机，管理端通过 Compose 内网访问。
- 管理端只监听宿主机 `127.0.0.1:7864`，先使用 SSH 隧道访问。
- 管理端和上游挂载相同的 `upstream/`，账号文件与配置不会读串。
- 挂载的是配置所在**目录**，可以接收管理端对配置文件的原子替换。
- 未挂载 `/var/run/docker.sock`，避免将宿主机高权限交给 Web 面板。
- `depends_on` 只保证启动顺序，不要求上游已经健康；首次没有账号时也能进入面板添加账号。
- 以上容器名必须未被其他部署占用；不要为了腾名字删除已有业务容器。

首次构建需要下载基础镜像、安装依赖、编译前端和上游，可能需要较长时间。

### 可选：国内网络构建加速

若依赖下载失败，可分别给管理端和上游传构建参数：

```bash
cd /opt/workbuddy-stack

docker compose build \
  --build-arg DEBIAN_MIRROR=mirrors.aliyun.com \
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  --build-arg NPM_REGISTRY=https://registry.npmmirror.com \
  --build-arg DOCKER_CLI_BASE=https://mirrors.aliyun.com/docker-ce \
  workbuddy-manager

docker compose build \
  --build-arg GOPROXY=https://goproxy.cn,direct \
  --build-arg APK_MIRROR=mirrors.aliyun.com \
  workbuddy2api

docker compose up -d
```

镜像参数仅影响对应下载源，不解决 Docker Hub 基础镜像拉取、GitHub 源码或 Compose 插件下载的所有问题。镜像不可用时可改回官方源或配置可信的网络代理；不要关闭 TLS 或跳过补丁校验。

## 6. 检查启动与首次密码

```bash
cd /opt/workbuddy-stack

docker compose ps
docker compose logs --tail=100 workbuddy-manager
docker compose logs --tail=100 workbuddy2api

curl -fsS http://127.0.0.1:7864/api/healthz

docker compose exec workbuddy-manager \
  cat /app/data/initial-admin-password.txt
```

首次用户名为 `admin`，密码随机生成。登录后立即修改密码；修改后初始密码文件可能删除，之后应使用你设置的密码。

没有导入账号时上游可能显示 `unhealthy`，因为没有可用于调用的账号；导入有效账号后再检查。若仍不健康，查看上游日志确认原因，不要直接关闭健康检查。

### 核验账号隔离协议

```bash
cd /opt/workbuddy-stack

docker compose exec -T workbuddy-manager python - <<'PY'
import json
import urllib.request
from server import config

request = urllib.request.Request(
    config.WB2API_BASE + '/status',
    headers={'Authorization': 'Bearer ' + config.upstream_api_key()},
)
with urllib.request.urlopen(request, timeout=10) as response:
    status = json.load(response)

version = status.get('account_scope_version')
print('账号隔离协议版本：', version)
if version != 1:
    raise SystemExit('账号隔离协议未就绪，请检查上游补丁及镜像构建。')
PY
```

应输出 `账号隔离协议版本： 1`。管理端对需要账号隔离但上游不支持的请求会拒绝，不会回退到无隔离调用。

## 7. Windows 通过 SSH 隧道访问

在你的 Windows PowerShell 中执行，替换实际 SSH 用户和服务器地址：

```powershell
ssh -N -L 17864:127.0.0.1:7864 用户名@服务器IP
```

保持终端运行，浏览器打开：

<http://127.0.0.1:17864>

同一台 Windows 上的客户端可设置 OpenAI Base URL：

```text
http://127.0.0.1:17864/v1
```

客户端使用**面板创建的 Key**，不是管理员登录密码，也不是上游内部 `api_key`。关闭 SSH 终端后隧道失效。

不要因为公网访问失败就直接把 `7864` 暴露出去；上面的本机监听是刻意设置的安全边界。

## 8. 导入账号、迁移旧库与创建 Key

### JSON 导入

打开「账号 → JSON 导入」，粘贴单账号、数组或旧版导出 JSON；也可用「导入/导出账号」上传文件。默认跳过重复账号，需要替换凭证时再勾选覆盖。

### SQLite 迁移

1. 在旧版停止写入后复制 SQLite，或使用 SQLite backup 导出完整快照。
2. 在面板打开「账号 → 数据迁移」。
3. 上传 `.db` / `.sqlite` / `.sqlite3`（最大 1 GB（1024 MB））。
4. 选择是否覆盖账号、是否迁移历史调用日志。
5. 先点「预览数据」，确认数量和警告，再点「确认导入」。

**WAL 注意：**运行中的旧版可能把最新数据写在 `-wal` 文件。单独复制 `.db` 即使可打开，也可能缺数据。不要把 `.db-wal` 改名成 `.db` 上传。

### Key 与账号权限

- 「密钥 → 新建密钥」默认随机生成；开启「自定义 Key」可填写 16–256 位字母、数字、下划线或连字符，不自动添加前缀。
- Key 创建后可在列表反复点击「查看」并复制。
- Key 的账号范围和账号侧允许的 Key 名单同时生效，按交集判断。
- 未选择账号范围的 Key 仅使用未被专属绑定占用的公共账号。

完整规则、保留字段和迁移边界见 [导入与权限使用说明](import-migration-account-access.md)。

### 手动重启上游

本文未挂载 Docker socket。迁移完成或保存需要重启的上游配置后，在 Linux 宿主机执行：

```bash
cd /opt/workbuddy-stack
docker compose restart workbuddy2api
```

账号热加载并不等于所有配置都能热更新。面板提示无法重启或需要宿主机操作时，按上面的命令执行。

容器日志同样在宿主机查看：

```bash
docker compose logs -f --tail=100 workbuddy2api
```

Docker socket 可让面板控制容器，但接近于交出宿主机 root 权限，**不作为默认方案**。本指南的外层双服务 Compose 也不应由上游目录内那份单服务 Compose 替代；日常构建和更新统一在 `/opt/workbuddy-stack` 执行。

## 9. 备份

至少备份：

- `manager/data/`：数据库、Key 和管理员信息。
- `upstream/config.json`：上游内部 Key 与配置。
- `upstream/auths/`：账号凭据。
- `upstream/data/`：上游状态。
- `compose.yml`：服务与卷配置。

一致性备份示例，操作期间服务会短暂停止：

```bash
cd /opt/workbuddy-stack
(
  set -e
  umask 077
  mkdir -p /root/workbuddy-backups
  docker compose stop
  trap 'docker compose start' EXIT
  backup="/root/workbuddy-backups/workbuddy-$(date +%Y%m%d-%H%M%S).tar.gz"
  tar -czf "$backup" \
    compose.yml manager/data \
    upstream/config.json upstream/auths upstream/data
  echo "备份完成：$backup"
)
```

备份含全部凭据，离机保存时应加密，不要放进公开仓库。恢复时先停止容器，确认正确的目标目录和备份版本，再恢复文件并设置 UID `10001` 权限。

`docker compose down` 本身不删除这里的 bind mount 目录；手工删除目录会丢数据，不要将删除数据当作普通重启方法。

## 10. 更新此定制版

**不要启用官方自动更新，也不要直接点官方一键更新**，否则可能覆盖自定义功能。使用本仓库源码更新：

```bash
cd /opt/workbuddy-stack

# 先按上一节备份。
git -C manager status --short
git -C manager pull --ff-only origin main

# 有同名备份时会停止，不会覆盖。
if [ -d manager/web/out ]; then
  mv -Tn manager/web/out "manager/web/out-before-update-$(date +%Y%m%d-%H%M%S)"
  test ! -d manager/web/out
fi

python3 manager/dev/apply_upstream_patches.py \
  /opt/workbuddy-stack/upstream

chown -R 10001:10001 manager/data upstream
docker compose up -d --build
docker compose ps
```

说明：

- `--ff-only` 遇到分叉会停止，不会强制覆盖本地代码。
- Dockerfile 优先使用现有 `web/out`；本指南默认不在宿主机生成它。若后来手动构建过，先移开旧产物，避免镜像继续打包旧 UI。
- 补丁脚本支持重复执行。若新版本要求不同的基础上游快照，请按该版本说明迁移，不能直接把新压缩包覆盖到含 `auths/`、`data/`、`config.json` 的运行目录。
- 更新后重新核验账号隔离版本，并测试 JSON 导入、Key 查看和客户端调用。

### 已部署用户更新本次「1 GB 迁移与管理员改名」改动

这次不需要更换 Go 上游，仅重建管理端。先按上一节备份，再在 Linux 宿主机执行：

```bash
sudo -i
set -e
cd /opt/workbuddy-stack

git -C manager pull --ff-only origin main

# 防止旧的宿主机前端产物被 Dockerfile 优先打包。
if [ -d manager/web/out ]; then
  mv -Tn manager/web/out "manager/web/out-before-update-$(date +%Y%m%d-%H%M%S)"
  test ! -d manager/web/out
fi

docker compose build workbuddy-manager
docker compose up -d --no-deps workbuddy-manager
docker compose logs --tail=80 workbuddy-manager
```

更新后强制刷新浏览器（Ctrl+F5）。「数据迁移」应显示 **1 GB（1024 MB）**；在「设置 → 管理用户」可修改 `admin` 用户名，改名后用新用户名和原密码重新登录。新建/修改密码只要求至少 **8 位**，不要求字符组合；可使用纯数字或纯字母，但仍建议使用长随机密码，避免公网暴露管理入口。

迁移大文件时：

- 只有 SQLite 迁移接口放宽，普通管理 API 的 16 MB 限制与升级包限制保持不变。
- 上传前验证管理员会话；文件以最多 1 MB 的块复制到临时目录，历史日志每批 500 条读取，不再限制 10 万条。
- 浏览器请求超时延长到 30 分钟；预览和确认导入各上传一次，请保持页面和连接。
- 容器临时目录通常在 `/tmp`。上传暂存和读取副本可能同时存在，建议为 1 GB 文件预留至少 3 GB 临时磁盘，再额外预留目标数据库和备份空间。预览不会修改原文件。
- 如果反向代理先返回 413，请放宽代理的请求体上限；仅更新应用无法绕过代理/CDN 的限制。

Nginx 示例（加入管理端对应的 `server` 或 `location`，不要新建同名冲突规则）：

```nginx
# 1 GiB 文件加上 multipart 开销；精确的文件 1 GiB 限制由应用校验。
client_max_body_size 1100m;
client_body_timeout 1800s;
proxy_read_timeout 1800s;
proxy_send_timeout 1800s;
```

修改后检查并重载 Nginx：

```bash
nginx -t && systemctl reload nginx
```

若使用宝塔/1Panel，修改对应站点反向代理的上传大小与超时；若仍受 CDN 限制，可通过上文的 SSH 隧道直接访问管理端再迁移。不需要把管理端端口开放到公网。

## 11. 域名、HTTPS 与公网访问

SSH 隧道适合首次配置和个人访问。需要长期向其他设备提供服务时：

1. 配置域名与 HTTPS 反向代理，代理到管理端 `http://127.0.0.1:7864`，不代理上游 `7863`。
2. 为管理页面配置额外的访问控制，限制暴露范围；调用路径 `/v1/`、`/v2/` 也需要 TLS。
3. 反向代理须关闭流式响应缓冲、设置合适超时，并允许足够大小的上传请求（SQLite 上限 1 GB（1024 MB），还应预留 multipart 开销）。
4. 若启用 `WB_TRUST_PROXY: "1"`，代理必须覆盖写入可信的 `X-Real-IP`，不能直接信任客户端提供的头。
5. 若反向代理也运行在容器里，不要把它的 `127.0.0.1` 当作宿主机。应连接到同一 Docker 网络，通过服务名访问管理端。

具体反向代理模板和子路径部署说明参见 [原部署文档](../deploy/README.md)。本文默认根路径部署，不能只修改运行时前缀而不重建前端。

## 12. 常见问题

| 现象 | 检查与处理 |
| --- | --- |
| 页面没有 JSON 导入、自定义 Key 或迁移入口 | 确认克隆的是 `Wenzixi2010/workbuddy-manager`；检查提交是否最新；排除旧 `web/out`；重新构建管理端 |
| `unable to open database file` / 导入权限错误 | 核对 bind mount 路径以及目录 UID `10001` 的权限 |
| 未导入账号时上游 `unhealthy` | 先添加有效账号；不是必须等待健康才可进入面板 |
| 导入后仍显示 0 个账号 | 核对共享路径、账号文件格式/权限及上游日志，必要时重启上游 |
| 账号隔离协议显示空值或提示不支持 | 确认补丁 0051 成功应用，重新构建上游；不能仅重启旧镜像 |
| 没有健康账号 / 503 | 检查 Key 范围与账号允许名单的交集、禁用/过期/冷却和余额；不会自动借用范围外账号 |
| 自定义 Key 提示重复 | 同值的已停用、过期或迁入 Key 也算重复；换一个值，不要删除仍在使用的 Key |
| 面板不能重启上游或读取容器日志 | 本文未挂载 Docker socket，请在宿主机执行 Compose 命令 |
| 公网无法直接访问 7864 | 正常：仅绑定本机；使用 SSH 隧道或 HTTPS 反向代理 |
| 构建在下载阶段卡住 | 查看具体失败源，配置网络/可信镜像；不要关闭 TLS 校验 |
| 新建账号或迁移后出现短暂连接失败 | 上游可能尚未完成启动/加载；检查日志并重试，持久化数据不会因容器重建而消失 |

---

指南依据本仓库 Dockerfile、配置项与账号隔离补丁编写。源码已有后端、Go 和前端构建验证；具体 Linux 主机上的 Docker 拉取、网络和权限仍需按本文现场检查。
