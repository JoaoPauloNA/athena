# MCP 工具参考

🇺🇸 [EN](../en/mcp-tools.md) · 🇧🇷 [PT-BR](../pt-BR/ferramentas-mcp.md)

服务器通过 stdio 使用 MCP 协议(`python -m athena.mcp_server`)。所有响应均为文本内容块中的 JSON。

## `list_providers`

列出所有已注册的 CLI:可用性、解析后的二进制路径、默认角色、实时模型目录、推荐默认模型、评分。

**输入:** `{}`

## `ask_provider`

向指定 provider 发送任务。prompt 会被包裹在 10 项报告契约中。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `provider` | string | ✅ | `codex`、`agent`、`claude`、`agy`、`openclaude`、`opencode`、`ollama` |
| `prompt` | string | ✅ | 任务内容 |
| `model` | string | | 模型 id(缺省回退到 provider 推荐默认值) |
| `working_directory` | string | | 执行器的项目目录 |
| `timeout` | integer | | 秒(缺省用 provider 默认值) |
| `skip_permissions` | boolean | | 传递 CLI 的免确认标志 |
| `verify` | boolean | | 启用**测谎仪**(见下文) |

**使用 `verify=true` 时:** 执行完成后,可用的最便宜验证模型(优先 OpenCode 免费模型,绝不与执行器同 provider)会将报告与 git 证据和引用文件交叉核对。FALSE 的报告连同原因退回执行器一次;第二次 FALSE 返回 `verdict.escalado=true`,由编排者决策(更换 CLI、拆分任务、中止)。

**响应附加字段:** `report_format_ok`、`warnings[]`(包括"简单任务使用重量级模型"的经济提示)、`verdict`(验证时)。

## `run_combo`

通过 combo 的故障转移链执行 prompt。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | string | ✅ | 任务内容 |
| `combo_id` | string | | 默认:`default` |
| `working_directory` | string | | |
| `timeout` | integer | | 每步超时覆盖 |

超时或出错时,按 combo 的故障转移策略尝试链中的下一步。

## `deliberate`

并行咨询多个代理并返回全部响应。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | string | ✅ | |
| `providers` | string[] | | 默认:`["agent", "agy", "claude"]` |

## `recommend`

"该叫谁?" 将每周刷新的评分表与机器上实际安装的模型结合。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task` | string | ✅ | 自然语言任务描述 |
| `task_type` | string | | 强制角色:`frontend`、`backend`、`raciocinio`、`rapidez` |
| `top_n` | integer | | 推荐数量(默认 3) |
| `only_installed` | boolean | | 仅推荐已安装模型(默认 true) |

**响应:** 检测到的角色、估算复杂度(`simple|medium|complex`)、排序推荐(`provider` + `model_id`,可直接传给 `ask_provider`)、列出被排除重量级模型的经济说明,以及可直接使用的 `dica`(建议字符串)。

## `refresh_models`

重新扫描各 CLI 的模型目录(`--list-models`、`opencode models`……)并重写缓存。

| 参数 | 类型 | 说明 |
|---|---|---|
| `force` | boolean | 忽略 TTL(默认 true) |

## `list_usage`

按 provider 计数:调用次数、总时长、估算 token、最近使用时间。
