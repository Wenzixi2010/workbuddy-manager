# 下一会话 SSH 部署交接

用户授权：下一会话提供服务器连接信息后协助部署；源码先推 GitHub，服务器从 GitHub 拉取。代理不可用/网站无法连接时告知用户，由用户解决，不绕过代理或关闭 TLS 验证。

## 源码

本机 `D:/Desktop/AppDev/workbuddy-manager`，远端 `https://github.com/Wenzixi2010/workbuddy-manager`，分支 main。先核对远端提交包含 `server/services/nativebackup.py`，不要假定未推送改动已在服务器。

定制功能：JSON 账号导入、旧版迁移、自定义 Key/再查看、双向账号权限；模型测试发 Ciallo；SQLite 可恢复备份含账号凭据和业务数据。恢复保留本机管理员/控制面/环境/上游 URL/路径，不安装上传 SQL；多分组需先有同名目标。

## 服务器预期，登录后核实

用户之前按 MD 安装过一次。预期 `/opt/workbuddy-stack`，外层 compose.yml 双服务 workbuddy2api、workbuddy-manager，源码分别 upstream/、manager/。管理端一般绑定 127.0.0.1:7864，上游 7863 不公开。

接收 SSH 主机、端口、用户名和认证方式。优先本机现有 SSH key/agent，不让用户把私钥正文发到聊天。只读检查真实路径、Git 状态、Compose、容器、反代和挂载，不输出配置里的 Token/密码。

## 操作顺序

1. 检查发行版、磁盘空间、Docker daemon、Compose、当前健康、Git remote/branch/status。
2. 一致性备份 manager/data、上游 auths/data/config 和 compose/override/.env。备份含全部凭据，权限 0600，不上传 GitHub；停机备份后及时启动。
3. 如果曾解压源码包，服务器可能大量未提交修改：先保存差异/未追踪文件，再逐文件比较与安全合并，不强行清理或覆盖用户手工修改。安全时才从 origin/main fast-forward 拉取。
4. 按 docs/upgrade-docker-control.md opt-in 配 socket + 实际 GID 的 group_add，保留现有端口/卷/环境。已有挂载不重复覆盖，说明高权限风险。
5. 旧 web/out 改名留档；外层 Compose 构建管理端，使用 up --no-deps --force-recreate。restart 不应用新挂载/镜像。原账号隔离补丁已存在则不改上游；缺版本1才按原补丁流程处理。
6. manager 内 docker version 同时显示 Client/Server；检查容器名、管理端健康/日志和账号隔离补丁。重载可能短暂停服务，Ciallo 测试消耗积分，执行前说明；不通过覆盖生产账号来验收恢复功能。
7. 确认管理员页面有模型测试/恢复备份入口，敏感备份不打印内容。保持 SSH 隧道/受保护 HTTPS，不为访问失败公开端口。

## 网络失败

定位 SSH/DNS/GitHub/Registry/npm/PyPI/代理/反代证书中的失败链路，收集脱敏错误告知用户。用户说代理/访问问题由他解决：停止该步骤等待修复，不擅自换代理/忽略证书。安全的其它只读检查可继续。

不要启用官方一键更新/自动更新，避免覆盖定制功能。恢复边界见 docs/sqlite-export.md。

## 后续新增功能

Key 列表显示绑定账号及权限范围内可用账号；模型页支持默认上游指定单账号 Ciallo 测试，不回退、只限管理员会话。参见 docs/key-account-list-and-tests.md。部署后校验列表字段 account_access 和 ChatIn.account_id；真实测试一条即可，不反复耗积分。

## 最新调整

Key 列表已精简为只有独占绑定才显示账号，不显示可使用账号；新增模型分组与 Key 选组。旧模型白名单迁入分组；模型测试弹窗内选择账号并手动发 Ciallo。本轮用户明确不用测试：部署时只构建、备份、重建和必要健康检查，不执行测试或消费模型请求。
