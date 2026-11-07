# 外部工具的模型目录与思考强度

面板提供三种模型目录：

- `GET /v1/models`：保持 OpenAI 兼容的 `{object: "list", data: [...]}`，补齐
  第三方工具读取的思考档位元数据；
- `GET /v1/_models`：提供 Codex 原生的 `{models: [...]}`，用于支持显式远程
  模型目录的 Codex 或兼容工具；
- `GET /v1/_dsh_models`：提供 `{models: [...], warnings: [...]}`。`models` 是可放入
  DSH provider profile 的模型配置条目，`warnings` 提醒当前固定菜单无法表示的档位。

三者都使用面板 Key 鉴权，并沿用同一套上游、账号作用域、版本、模型白名单和别名
过滤。Key 绑定具名上游时，目录来自该上游，不用默认账号池的目录裁掉它的模型。
没有设置模型分组或白名单的 Key 仍可使用其作用域内的全部模型。

本功能只解决工具的**目录发现、菜单显示和选择值传递**。不新增模型能力配置表、
面板会话存储或完整多协议系统；不凭模型名称推测可用档位。普通及 Codex 目录保留
原档位字符串；DSH 配置仅做下述固定菜单到实际 wire 值的兼容映射。

## OpenAI 兼容目录：字段口径

面板与上游是两个独立进程。旧上游可能缺少第三方工具读取的扩展字段，因此面板
在自己的 `/v1/models` 上做展示层补充，不把它变成第二个模型能力事实来源。

| 字段 | 补充规则 |
|---|---|
| `modalities` | 缺字段时补 `{"input": ["text"]}`；`supports_images == true` 时追加 `"image"` |
| `supported_reasoning_levels` | 缺字段且有 `reasoning_supported_efforts` 时，按原顺序派生 `[{"effort": e, "description": "…"}]`；已有条目只补缺少的描述 |
| `supports_reasoning` | 有实际档位且字段缺失时补 `true`，不覆盖上游已有值 |
| `default_reasoning_level` / `default_reasoning_effort` | 缺字段时，仅由属于支持档位列表的可信默认值补充；不自行选择 `high` 或最高档 |

示例：

```json
{
  "object": "list",
  "data": [
    {
      "id": "global:example-think",
      "reasoning_supported_efforts": ["low", "high", "max"],
      "reasoning_default_effort": "high",
      "supported_reasoning_levels": [
        {"effort": "low", "description": "Low reasoning effort"},
        {"effort": "high", "description": "High reasoning effort"},
        {"effort": "max", "description": "Maximum reasoning effort"}
      ],
      "default_reasoning_level": "high",
      "default_reasoning_effort": "high",
      "supports_reasoning": true
    }
  ]
}
```

约束：

1. 保留上游已有的档位、描述和默认值；不覆盖已有能力声明。
2. 只有 `supports_reasoning: true`、模型名称或单个默认值而没有支持列表时，不能据此
   编造一组可选档位。
3. 无法识别的响应结构和缺少模型 `id` 的条目原样透传；上游错误不转换为空模型列表。
4. 国内版 `cn:` 前缀按现有规则去除；国际版保留 `global:`，避免选中后路由到国内账号池。

实现了这些扩展字段读取的工具可以使用普通 `/v1/models`。**当前核对的 DSH NEXT
`2.0.18-next` 例外**：其 HTTP `readListing` 只读取模型 ID、名字、上下文与输出上限，
忽略 HTTP 目录中的思考字段。因此，仅刷新普通模型列表不会让这个版本出现思考菜单，
需要使用下一节的 DSH 模型配置导出。

## DSH NEXT：导入模型配置以显示菜单

DSH NEXT `2.0.18-next` 的思考菜单实际读取 provider 的
`models[].reasoningEfforts` 映射，而不是 HTTP `/v1/models` 的扩展字段。
选择的菜单档位会由 DSH 自己随会话保存。本功能无需新增面板会话表。

### 下载配置条目

使用与该 provider 相同的面板 Key 读取 `GET /v1/_dsh_models`。响应只包含模型配置和
警告，**没有 Key、base URL 或 provider 统一默认档**，不应作为整个 provider profile
或整个配置文件直接导入。

先以你现有的安全方式设置 `WORKBUDDY_API_KEY` 环境变量，不在命令中写明文 Key。
PowerShell 示例：

```powershell
if (-not $env:WORKBUDDY_API_KEY) { throw '先设置 WORKBUDDY_API_KEY 环境变量' }
$downloadPath = Join-Path (Get-Location) 'workbuddy-dsh-models.json'
Invoke-WebRequest -Uri 'https://panel.example/v1/_dsh_models' -MaximumRedirection 0 `
  -Headers @{ Authorization = "Bearer $env:WORKBUDDY_API_KEY" } `
  -OutFile $downloadPath
$dshCatalog = Get-Content -LiteralPath $downloadPath -Raw | ConvertFrom-Json
$dshCatalog.warnings | ConvertTo-Json -Depth 20
ConvertTo-Json -InputObject @($dshCatalog.models) -Depth 30
```

Linux/macOS 可下载后用编辑器检查响应：

```sh
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${WORKBUDDY_API_KEY}" \
  'https://panel.example/v1/_dsh_models' \
  -o 'workbuddy-dsh-models.json'
```

### 只替换目标 profile 的 models

这个版本的模型设置 UI 没有高级 JSON 粘贴／导入入口，需手工编辑实际使用的
`cordis.patch.yml`。DSH 的 **设置 → 打开配置文件** 按钮会打开当前 profile 的这个文件，
不是旧版 `settings.yaml`，无需猜测配置路径。

1. 先用 **设置 → 打开配置文件** 定位当前 `cordis.patch.yml`，再退出 DSH，并备份该文件。
2. 找到 `llm-pi-ai` 插件下需要修改的 provider profile。下面只是定位结构示例，
   **不是让你用它覆盖整个文件**：

   ```yaml
   - id: llm-pi-ai
     name: '@deepseek-ai/dsh-llm-pi-ai'
     config:
       providers:
         workbuddy:
           api: openai-responses
           baseURL: https://panel.example/v1
           apiKeyEnv: WORKBUDDY_API_KEY
           models: [] # 仅将这里替换为下载响应的 models 数组
   ```

3. **只替换这个已存在 profile 的 `models` 值**。下载响应的 `models` JSON 数组可原样
   作为 YAML 中的 `models` 值；复制数组本身，不要复制外层 `{models, warnings}`。
   保留原有 `api`、`baseURL`、`apiKeyEnv`、`headers`、其他 providers 和插件条目。
4. 检查 `warnings`，保存后重新打开 DSH，选择模型，再选择该模型的思考档位。

导出只基于 Key 当前可见模型。Key 分组、白名单、账号作用域或上游能力改变后，要重新
下载并替换该数组。若需回滚，恢复刚才备份的配置文件。

注意：**恢复默认模型** 会清除手工补入的思考能力。重新抓取模型列表时，同 ID 已有的
手工能力会保留；新出现的模型 ID 没有这份能力配置，需要重新导出并更新 `models`。

### 固定七档、默认行为与限制

当前 DSH UI 的固定档位为 `off`、`minimal`、`low`、`medium`、`high`、`xhigh`、`max`。
导出的 `reasoningEfforts` 只包括该模型实际声明且能由这个菜单表示的档位：

| 实际上游档位 | DSH 菜单键 → 发送值 |
|---|---|
| 已声明的七档之一 | 保持同名键和原值 |
| `none` | 没有原生 `off` 时，映射为 `off: "none"` |
| `ultra` | 没有原生 `max` 时，映射为 `max: "ultra"` |
| 与原生 `off` / `max` 冲突，或其他自定义值 | 不覆盖原生键、不猜近似档位，通过 `warnings` 提醒 |

因此，这个 DSH 版本不一定能显示所有自定义 wire 名称；应查看 `warnings`，不能把
缺失菜单选项理解成上游不支持。普通及 Codex 目录仍保留这些原始非空档位字符串。

不要为本功能额外添加统一的 provider `reasoning` 默认档，避免多个模型的支持列表
不同却使用同一个默认档。

DSH 的“Default／Off”有既有行为差异：没有 `off` 映射时，Default 不发送思考字段，
继续使用上游默认；有 `off` 字符串映射时，Default 和 Off 都会发送该 wire 值。
因此不能承诺“Default 始终不发送参数”，也不能仅凭 UI 名称承诺模型真的关闭思考。

### 按现有 api 检查发送开关

手工替换 `models` 时**保留目标 profile 原有的 `api`**。`/v1/_dsh_models` 只导出模型
片段，不知道客户端选用的协议，也不会猜测或生成 provider 级 `compat` 设置。
菜单可见与请求是否发送思考字段是两件事：

- **`api: openai-responses`**：沿用上面的示例，不添加
  `compat.supportsReasoningEffort` 或 `compat.thinkingFormat`。当前 DSH 的 Responses
  配置会拒绝这些 Chat 专用字段。
- **`api: openai-completions`**：DSH 的 Chat adapter 需要在目标 provider profile
  的 `compat` 中设置以下两个字段，才会发送 `reasoning_effort`：

  ```yaml
  # 与目标 provider 的 api / models 同层级；仅合并这两个字段
  compat:
    supportsReasoningEffort: true
    thinkingFormat: openai
  ```

  如果已经有 `compat`，只设置／更新这两个键，保留其中其他键及现有 `headers`、鉴权和
  其他 profile 配置。不要用这个片段覆盖整个 provider，也不要复制到 Responses profile。

## Codex 原生目录

Codex 的模型目录不是普通的 `data[]`。它读取 `models[]`，每个模型用 `slug` 作为调用
时的模型名，并读取 `supported_reasoning_levels` 和 `default_reasoning_level`。
面板的 `/v1/_models` 将已经过滤好的目录序列化成这个结构；`slug` 使用最终模型 ID，
白名单别名不会变回上游真实名字。
Codex 导出与深链初选也保留实际请求别名：例如国际分组中的 `global-alias` 仍是
`global-alias`，不会自动变成 `global:global-alias`；国际真模型仍保留 `global:` 路由前缀。

原生 schema 还要求编程代理的基线指令。上游提供有效 native instructions 时保留原值；
未知模型没有该元数据时，使用随包保存的 **Codex 官方 fallback 基线**，不是空 system
prompt 或面板自行编造的提示词。源文件及其 `LICENSE` / `NOTICE` 位于
`server/services/codex_catalog/`，与已核对客户端默认未知模型的约 20 KiB 基线一致。
该兼容基线不代表上游声明了额外能力。

显式远程目录响应不得超过 **1 MiB**。超过限制时面板返回
`model_catalog_too_large`，而不是悄悄截断模型或清空指令；请给该 Key 选择包含更少模型
的分组，再重新读取目录。

### 新导出的配置

在面板导出 **CC-Switch → Codex** 配置时，生成的 provider TOML 已包含
`model_catalog_url`，国内版选中模型也与目录裸名对齐。使用支持该设置的客户端导入后，
重新进入客户端读取目录，再通过模型菜单选择模型和思考档位即可。
本机“一键导入”的深链和直接写入也共用该配置；深链的独立 `model` 参数与 TOML
保持一致，不会把国内裸名重新覆盖成 `cn:`。

### 已有配置

在原 provider 配置节中增加 `model_catalog_url`，不要把 `/v1/models` 的普通
OpenAI 响应当作 Codex 原生目录。模型名以实际目录为准；旧配置中的国内 `cn:` 模型名
应改成目录里的裸名，国际 `global:` 不要去掉。示例：

```toml
model_provider = "workbuddy"
model = "global:example-think"

[model_providers.workbuddy]
name = "WorkBuddy"
base_url = "https://panel.example/v1"
wire_api = "responses"
requires_openai_auth = true
model_catalog_url = "https://panel.example/v1/_models"
```

鉴权沿用现有的面板 Key 配置；不要把上游凭据放进客户端。`model_catalog_url` 是完整
URL，Codex 直接访问它，不会再追加 `/models`。子路径部署应保留子路径，例如
`https://panel.example/workbuddy/v1/_models`。

### 客户端版本与排查

- **需要支持 `model_catalog_url` 的客户端版本。** 已核对 Codex CLI `0.144.6` 不支持
  该设置且不会为 API-key 自定义 provider 自动发现远程目录；仅加这一行不会让旧版本
  生效，应升级客户端或使用下述本地目录快照。桌面 bundled Codex
  `0.166.0-alpha.5.1` 已有远程目录能力。
  这里的核对版本不是承诺最低版本，其他版本或 DSH 分支应以其实际实现为准。
- 在支持该功能的版本中，`features.api_key_model_discovery` 默认开启；检查是否被用户
  配置显式设为 `false`。
- 有效的 `model_catalog_json` 本地目录覆盖会优先于远程发现。若希望使用面板的实时
  目录，移除该覆盖或改用正确的本地目录，再重新进入客户端。
- 只填写 `base_url` 和 API Key 不等于开启 API-key 自定义 provider 的远程目录发现，
  需要上面的显式 URL。
- 模型名必须与目录中的 `slug` 一致。国内版使用目录中的裸名；国际版保留 `global:`；
  使用白名单别名时选择别名。
- 原生目录使用严格 schema，且客户端可能限制响应大小、拒绝重定向；不要把登录页、
  反向代理重定向或普通 OpenAI 目录 URL 配成 `model_catalog_url`。

### 旧版 Codex：本地目录快照

Codex CLI `0.144.6` 支持顶层 `model_catalog_json`。可以下载同一份 `/v1/_models`
响应到本地，再指定该文件；不需要改网关或发送模型测试请求。以下命令只读取目录，
要求先以你现有的安全方式设置 `WORKBUDDY_API_KEY` 环境变量，不在命令中写明文 Key。

PowerShell 示例：

```powershell
if (-not $env:WORKBUDDY_API_KEY) { throw '先设置 WORKBUDDY_API_KEY 环境变量' }
$catalogPath = Join-Path $HOME '.codex\workbuddy-models.json'
Invoke-WebRequest -Uri 'https://panel.example/v1/_models' -MaximumRedirection 0 `
  -Headers @{ Authorization = "Bearer $env:WORKBUDDY_API_KEY" } `
  -OutFile $catalogPath
$catalogPath
```

Linux/macOS 示例：

```sh
curl --fail --silent --show-error \
  -H "Authorization: Bearer ${WORKBUDDY_API_KEY}" \
  'https://panel.example/v1/_models' \
  -o "$HOME/.codex/workbuddy-models.json"
```

确认下载成功后，在 Codex 配置**顶层**填写实际输出的绝对路径，不能放进 provider 节：

```toml
model_catalog_json = 'C:\Users\your-user\.codex\workbuddy-models.json'
```

Linux/macOS 可填写例如 `'/home/your-user/.codex/workbuddy-models.json'`。鉴权和 provider
配置仍沿用原设置，选中模型应与文件中的 `slug` 一致。本地文件是**静态快照**：
Key 的模型分组、白名单、账号作用域或上游模型发生变化后，需要重新下载。
该文件不包含 Key，但包含该 Key 可见的模型元数据，应按自己的配置文件妥善保存。
以后升级到支持远程目录的客户端时，移除 `model_catalog_json` 才会使用
`model_catalog_url` 的实时目录。

## 选中的档位如何发送

工具使用 Responses 协议时发送 `reasoning: {"effort": "high"}`，面板现有转换链路将
该字符串传入 Chat Completions 的 `reasoning_effort`，流式和非流式沿用同一份请求。
使用普通 Chat Completions 的工具可以直接发送 `reasoning_effort`。

面板保留**客户端实际发来的**档位字符串。部分 Codex 版本会在客户端内部处理
`ultra`、`persistent` 等特殊档位，因此不能保证菜单里的所有拼写都原样出现在请求中；
这不是面板新增的改名或降级行为。

目录中的默认档只是模型能力元数据；本次没有新增网关默认档注入或降级规则。
“默认／不指定”的具体请求行为由客户端决定。最终上游行为仍由所选渠道的实现决定，
本功能不把未声明的档位视为支持，也不声称新增了原长方案中的严格校验或预算映射。

## 实现位置与官方依据

- `server/services/toolmodels.py`：普通能力字段补充、工具目录序列化；
- `server/services/codexschema.py`：清理严格 native schema 中损坏的可选元数据；
- `server/services/dshmodels.py`：导出 DSH 的模型思考档位配置及兼容警告；
- `server/services/codex_catalog/`：Codex 官方基线指令及许可说明；
- `server/routers/gateway.py`：三种目录共用鉴权和作用域过滤；
- `server/services/keyexport.py`：Codex 导出配置附带显式目录 URL；
- `server/tests/test_tool_models_api.py`、`server/tests/test_tool_models.py`、
  `server/tests/test_codexschema.py`、`server/tests/test_dshmodels.py`：目录、权限、
  思考档位及流式／非流式传参的离线回归用例。

官方 OpenAI documentation：

- [Codex model catalogs](https://developers.openai.com/codex/model-catalogs)
- [Codex config reference](https://developers.openai.com/codex/config-file/config-reference)
