# 现有部署升级：启用上游自动重载与模型一键测试

适用于之前按 [Linux 双服务部署指南](linux-docker-compose.md) 部署到 `/opt/workbuddy-stack` 的实例：外层 `compose.yml` 的服务名为 `workbuddy2api` 和 `workbuddy-manager`。账号、Key、管理员和迁移功能沿用现有持久化数据，不需要重新安装。

## 1. 备份并同步这次的定制代码

先按原指南「备份」小节备份 `manager/data/`、上游配置/账号/状态和 Compose 文件。有现成 `compose.override.yml`、`.env` 时也一起备份。

本文命令在 Linux 宿主机执行，默认已切换 root；非 root 用户按需给 Docker、文件写入等命令加 `sudo`。

```bash
cd /opt/workbuddy-stack

git -C manager status --short
# 仅在这次代码已经提交并推送到你的定制仓库后执行；不是官方仓库。
git -C manager pull --ff-only origin main
```

**本机未提交/推送的修改不会被服务器的 `git pull` 获取。** 也可以先通过 SCP/SFTP 同步这次定制源码到 `manager/`，但不要覆盖或删除 `manager/data/`、服务器 `.env` 和上游配置。

如果使用本次提供的源码包 `workbuddy-manager-source-20261105.zip`，先上传到服务器 `/tmp/`，备份后解压到原来的 `manager/`（源码包不包含 `.git`、`data/`、`.env`、上游运行配置或旧前端构建产物）：

```bash
unzip -o /tmp/workbuddy-manager-source-20261105.zip \
  -d /opt/workbuddy-stack/manager
```

这会覆盖同名定制源码，保留持久化数据和原 Git 目录；如果服务器源码有额外手工修改，先备份或合并，不要盲目覆盖。该方式会在现有 Git checkout 中形成未提交修改，后续 `git pull` 前需先处理这些改动。


当前源码包也包含「数据迁移 → 导出 SQLite」功能（含账号凭据的可恢复备份），范围与恢复边界见 [SQLite 导出说明](sqlite-export.md)。如果 Docker 挂载已按本文完成，这次只需更新源码并重新构建管理端，不必重复修改挂载。

确认同步成功：

```bash
test -f manager/web/lib/model-test.ts
test -f manager/server/services/sqliteexport.py
test -f manager/server/services/nativebackup.py
test -f manager/deploy/compose.docker-control.yml
```

仍须关闭官方自动更新，不能因为启用了 Docker 控制就使用官方一键更新：官方版可能覆盖账号隔离补丁和定制功能。所有手动 Compose 操作统一在外层 `/opt/workbuddy-stack` 执行。

## 2. 确认宿主 Docker 正常，读取 socket 的实际 GID

> **安全边界：**挂载 Docker socket 近似授予面板宿主机 root 权限。只在你信任的个人部署中启用，保持管理端 `127.0.0.1:7864` 监听并通过 SSH 隧道或受保护的 HTTPS 反代访问。不要用 `chmod 666`、`privileged: true` 或改成 root 用户来绕过权限问题；socket 加 `:ro` 也不能阻止 Docker API 创建高权限容器。

```bash
cd /opt/workbuddy-stack
docker info >/dev/null
test -S /var/run/docker.sock
stat -c '%g' /var/run/docker.sock
```

如果 `docker info` 本身失败，先修复/启动宿主 Docker；如果 socket 不在上述路径（如 rootless Docker），本指南不直接适用，要改成该实例的真实 socket 路径及 GID。

把真实 GID 写到**外层目录**的 `.env`，不要凭经验填写 998/999。下列命令仅更新 `DOCKER_GID`，保留其它变量：

```bash
export DOCKER_GID="$(stat -c '%g' /var/run/docker.sock)"
python3 - <<'PY'
import os
from pathlib import Path
p = Path('.env')
lines = p.read_text().splitlines() if p.exists() else []
lines = [line for line in lines if not line.lstrip().startswith(('DOCKER_GID=', 'export DOCKER_GID='))]
lines.append('DOCKER_GID=' + os.environ['DOCKER_GID'])
p.write_text('\n'.join(lines) + '\n')
p.chmod(0o600)
PY
```

## 3. 加上挂载与组权限（两项缺一不可）

保留原 `compose.yml` 不变，给管理端增加 overlay。

**没有 `compose.override.yml` 时：**

```bash
cd /opt/workbuddy-stack
# -n 不覆盖已有文件。若该文件已经存在，按下方说明手动合并。
cp -n manager/deploy/compose.docker-control.yml compose.override.yml
```

**已有 `compose.override.yml` 时不要覆盖，**在其中的 `workbuddy-manager` 服务下合并这两项，保留原来的环境变量、端口、其它 volumes 和 group_add：

```yaml
services:
  workbuddy-manager:
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    group_add:
      - "${DOCKER_GID:?Set DOCKER_GID to the host Docker socket GID}"
```

外层双服务方案还需确认原 `compose.yml` 的管理端环境变量是：

```yaml
environment:
  WB2API_BASE: http://workbuddy2api:7863
  WB2API_MODE: docker
  WB2API_CONTAINER: workbuddy2api
```

`WB2API_CONTAINER` 必须等于上游的实际 `container_name`，不是猜测的服务名。上一版教程设为 `workbuddy2api`，不要改成 `wb2api`。

`docker compose` 默认会合并外层 `compose.yml` 和 `compose.override.yml`。如果你习惯显式使用 `-f compose.yml`，要同时加 `-f compose.override.yml`；单独 `-f` 不会自动加载 overlay。

## 4. 构建并重建管理端容器

```bash
cd /opt/workbuddy-stack
docker compose config --quiet

# 避免复用旧版静态前端；Dockerfile 在检测到 web/out 时可能直接复用它。
if [ -d manager/web/out ]; then
  saved="manager/web/out-before-update-$(date +%Y%m%d-%H%M%S)"
  test ! -e "$saved" || exit 1
  mv -Tn manager/web/out "$saved"
  test ! -d manager/web/out || exit 1
fi

# 本次模型页和 Docker 提示仅改管理端，原上游的账号隔离补丁无需重新安装。
docker compose build workbuddy-manager
docker compose up -d --no-deps --force-recreate workbuddy-manager
```

**仅 `docker compose restart workbuddy-manager` 不够：**它不会应用新挂载、group_add 或新镜像，必须 recreate。重建不会删除 `./manager/data:/app/data` 和共享上游目录里的数据。

如果之前账号隔离补丁尚未安装，仍按原指南先运行补丁脚本并重新构建上游；不要仅凭模型测试成功就认为 Key/账号隔离已验证。

## 5. 验证挂载、权限和重载

```bash
cd /opt/workbuddy-stack
# 此处在容器里读 uid/补充组和 socket：应仍以 uid 10001 运行。
docker compose exec workbuddy-manager sh -c 'id; ls -l /var/run/docker.sock; docker version'

# 确认管理端能访问 Docker daemon 并找到教程中的上游容器。
docker compose exec workbuddy-manager docker inspect --format '{{.Name}} {{.State.Running}}' workbuddy2api

docker compose ps
docker compose logs --tail=100 workbuddy-manager
```

`docker version` 应同时显示 Client 和 Server。只有 Client、出现 `Cannot connect...` 或 `permission denied` 都不算成功。

登录面板，在上游设置保存一次确实需要生效的配置或点击已有重启入口，检查重载状态。如果想在宿主机直接验证管理端的重启权限（会短暂停服务）：

```bash
docker compose exec workbuddy-manager docker restart workbuddy2api
```

若出现找不到容器，核对 `WB2API_CONTAINER` 和上游 `container_name`；若是权限不足，重新核对宿主 GID、`group_add` 以及是否完成 recreate。不要把「配置已保存」误当成「上游已重载」。

## 6. 模型界面测试

刷新网页，用**管理员**登录后进入「模型」，每行右侧点击「测试」即可发送固定的 `Ciallo`，不用进入聊天测试台、不用手填文本或创建 Key。弹窗显示提示词、回复/推理内容、耗时、上游返回的 Token/积分或失败原因。访客不显示这个消耗积分的入口。

- 使用当前国内/国际版本；国际模型请求会由服务端正确加上 `global:` 前缀。
- 复用管理员聊天测试接口，消耗记在请求日志/用量里。
- 默认走公共账号池；模型页可选默认上游中的指定账号（含专属账号）做管理员单次测试，失败不回退，且不修改 Key 权限。这**不是某个 Key 的绑定/配额测试**。
- 关闭/取消弹窗会停止客户端等待，不能保证上游已经开始的生成不再消耗积分。
- 模型测试不依赖 Docker socket：socket 解决的是重载和容器控制，不是调用模型。

## 7. 撤销 Docker 控制（可选）

从 overlay 中删除 socket 挂载及对应 group_add，然后再执行 `docker compose up -d --no-deps --force-recreate workbuddy-manager`。保留原 overlay 的其它设置；不要误删数据挂载。面板将回到需在宿主机手动重启上游的模式。

Key 列表新增绑定/可使用账号，模型可选指定账号；详细口径见 [账号范围与指定测试](key-account-list-and-tests.md)。
