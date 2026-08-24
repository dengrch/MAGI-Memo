# DSH MAGI Memo 插件说明

`sages/dsh/magi-memo` 是一个本地 Cordis 插件。它把 MAGI Memo 的 HTTP API 包装成模型可见工具，并增加 Session 内可随时切换的主动记忆模式；模式动态控制 system prompt，而 Episode/Atom 的实际处理仍由 MAGI 完成。

## 整体结构

```text
DSH model
  ↓ tool call
index.ts：工具 JSON Schema、system prompt、输出渲染
  ↓ typed method
client.ts：参数检查、认证、超时、HTTP 请求与错误转换
  ↓ HTTP
MAGI Memo :3491：workspace、Episode、Atom、图谱与检索
```

插件目录只有四类关键文件：

- `src/index.ts`：Cordis 注册入口，声明配置、系统提示和五个工具。
- `src/client.ts`：MAGI HTTP client，负责调用 REST API。
- `cordis.yml`：把本地插件插入 DSH Web profile，并从环境变量读取配置。
- `tests/client.test.ts`：用本地 mock HTTP server 验证请求方法、路径、参数、认证和响应。

## Cordis 注册入口

`src/index.ts` 导出三个 Cordis 约定成员：

```ts
export const name = 'magi-memo'
export const inject = ['tools', 'systemPrompt']
export function apply(ctx: Context, config: Config) { /* registrations */ }
```

`inject` 表示插件依赖 DSH 的工具注册服务和 system prompt 服务。`apply()` 创建一个 `MagiClient`，然后注册 system prompt section 与工具。所有工具共用 JSON 输出渲染器；当结果超过 `maxModelOutputChars` 时，插件会截断传给模型的文本，避免一次 recall 占满模型上下文。

插件配置由 `@deepseek-ai/schemastery` 定义：

| 配置                    |                    默认值 | 作用                                  |
| ----------------------- | ------------------------: | ------------------------------------- |
| `baseUrl`             | `http://127.0.0.1:3491` | MAGI Memo 根地址                      |
| `apiKey`              |                        空 | 可选 API Key，以`X-API-Key` 发送    |
| `timeoutMs`           |                 `30000` | 单次 HTTP 请求超时                    |
| `maxModelOutputChars` |                 `30000` | 工具结果进入模型上下文前的最大字符数  |
| `defaultMemoryMode`   |                  `auto` | 尚未执行模式命令时的 Session 默认策略 |

插件加载时会拒绝空 `baseUrl`、非整数或小于 1 的超时和输出上限。

## Session 内动态 Memory Mode

插件注册 `/memory` 命令，用户可在同一个 Session 中随时执行：

```text
/memory auto
/memory manual
/memory off
/memory                 # 查看当前模式
```

模式不是启动参数，也不是创建 Session 时固定的聊天类型。每次模型请求前，插件都会从 Session 已持久化的 `command/run` 与 `command/done` 记录中折叠最后一次成功完成的 `/memory` 命令；失败、无效或尚未完成的命令不会改变状态。因此模式切换无需重启，并能随 Session 恢复和 fork。

- `auto`：每轮回答前执行 Recall Gate，自行判断是否需要召回并选择 `local/global/hybrid/naive/mix`；回答末尾执行 Write Gate，仅在出现新的长期信息时写入。
- `manual`：只有用户明确要求搜索、保存或更新记忆时才调用记忆工具。
- `off`：禁止 recall 和 memory write。

插件配置中的 `defaultMemoryMode` 只决定没有成功模式命令时的默认值。它不是日常切换入口；已有 Session 用 `/memory` 即时切换。

插件在 order `112` 动态注册 `magi-memo` prompt section，并在所有模式下继续约束：

- 所有记忆操作都作用于当前 active workspace。
- workspace 创建和切换只能在用户明确要求时调用；agent 不得主动改变 workspace。
- agent 不得自行切换 Memory Mode；模式是用户控制面。

`auto` 仍然不是“每轮强制调用”：Recall Gate 可以判断无需历史，Write Gate 也可以判断没有值得持久化的新信息。

DSH Web 提供两个等价入口：输入栏右侧的“记忆 · 自动/手动/关闭”选择器，以及输入裸 `/memory` 后弹出的自动/手动/关闭选项。两者都执行同一条 `/memory <mode>` 命令，并读取 host 根据 Session 日志生成的 `magiMemoryMode` 投影；因此网页、命令、刷新恢复和 fork 共用一份权威状态。这里没有为 DSH 命令系统增加通用 `hidden` 能力。旧日志中的 `/memory active` 会兼容折叠为 `auto`，但新命令不再接受 `active`。

## 五个模型工具

| DSH 工具                        | MAGI API                                                        | 行为                                                              |
| ------------------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------- |
| `magi_workspace_create`       | `POST /workspaces`，必要时 `POST /workspaces/{id}/activate` | 创建或复用精确匹配的 workspace，并激活                            |
| `magi_workspace_activate`     | `GET /workspaces` + `POST /workspaces/{id}/activate`        | 按精确 ID 或名称切换既有 workspace                                |
| `magi_memory_write_episode`   | `POST /documents/text`                                        | 写入原始 Episode，由 MAGI 执行完整抽取流程                        |
| `magi_memory_write_extracted` | `POST /memory/ingest/extracted`                               | 直接提交 Episode evidence、实体、关系和 Atom，跳过 extraction LLM |
| `magi_memory_recall`          | `POST /query/data`                                            | 返回结构化检索上下文，不让 MAGI 生成最终回答                      |

插件故意没有向 agent 注册 workspace 删除工具。删除 workspace 只能通过 MAGI WebUI 或直接 API 完成。

## Plugin 与 MCP 的区别

当前 `magi-memo` 是 **DSH plugin**：TypeScript 代码被 Cordis 直接加载到 DSH 进程中，由插件调用 MAGI 的 REST API，再把能力注册成 DSH tools。MCP 则是一个独立的客户端—服务器协议：MAGI 需要提供 MCP server，DSH 或其他 agent host 作为 MCP client，通过标准协议发现并调用 tools、resources 或 prompts。

| 维度          | 当前 DSH plugin                                                    | MCP 方式                                                               |
| ------------- | ------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| 运行位置      | `index.ts`、`client.ts` 与 DSH 同进程                          | MAGI MCP server 独立运行，host 通过协议连接                            |
| 工具注册      | `ctx.tools.register(defineTool(...))`                            | MCP server 声明 tools，MCP client 自动发现                             |
| System prompt | 可直接用 Cordis`ctx.systemPrompt.section()` 注入 DSH prompt      | MCP 可以暴露 prompts，但是否采用、放在何处由 host 决定                 |
| 后端通信      | 插件自行调用 MAGI REST API                                         | host 与 MCP server 使用 MCP transport；server 内部再调用 MAGI Core/API |
| 复用范围      | 针对 DSH 的工具、配置和生命周期                                    | 同一个 MCP server 可以被 DSH、Codex、Claude Desktop 等兼容 host 使用   |
| DSH 集成深度  | 高，可使用 Cordis 配置、tool renderer、执行 signal 和 prompt order | 受 MCP 与 host 暴露的标准能力限制，DSH 特有行为仍可能需要薄 plugin     |
| 部署          | 需要把本地 TypeScript 插件加入 DSH profile                         | 需要部署并配置独立 MCP server，以及连接方式和权限                      |
| 故障边界      | 插件异常发生在 DSH 进程内；MAGI HTTP 服务仍是外部依赖              | MCP server 故障与 host 隔离，但多一层进程、transport 和连接诊断        |
| 版本耦合      | 直接依赖 DSH/Cordis tool API                                       | 主要依赖 MCP 协议，host-specific 耦合较低                              |
| 权限策略      | system prompt 与工具描述可以紧密表达“仅用户授权时切换 workspace” | server 可以拒绝危险调用，但主动调用策略仍需要每个 host 正确配置        |

两种方式不会改变 MAGI 的核心数据语义：workspace 隔离、Episode/Atom 生命周期、Neo4j/NanoVector 投影、检索模式和删除机制都应继续由 MAGI 服务端拥有。区别主要在 agent host 如何发现和调用这些能力。

### 当前为什么适合 plugin

当前目标是让 DSH 快速、深度地使用 MAGI，因此 plugin 更直接：代码量小，可以同时注册工具和 system prompt，还能复用 DSH 的 tool renderer、取消信号及 `cordis.yml` 配置。MAGI 已有 REST API，plugin 只需做一层薄适配，不必额外维护 MCP server。

### 什么时候值得提供 MCP

当 MAGI 需要被多个不同 agent host 复用时，MCP 更合适。可以新增一个 MAGI MCP server，把 recall、Episode 写入和 extracted 写入定义为标准 MCP tools；workspace 创建、切换和删除仍应由 server 强制执行用户授权或管理端权限，不能只依赖模型提示。

实际演进可以同时保留两者：MCP 作为跨 host 的标准接口，DSH plugin 继续负责 DSH 专属 system prompt、调用策略和输出渲染，并把底层调用从 REST client 换成 MCP client。这样不会为了协议统一而丢失 DSH 的深度集成能力。

### Episode 写入

`write_episode` 接收 `content` 和可选 `source_id`。client 对 `source_id` 计算 SHA-256 并截取 32 位，生成稳定的 `magi-memo-<digest>.txt` 作为 `file_source`。没有 `source_id` 时使用内容本身计算，因此完全相同的内容会得到相同来源标识。

它适合自然语言较长、结构不确定，或希望 MAGI 自己完成实体、关系和 Atom 抽取的输入。

### Extracted 写入

`write_extracted` 要求至少包含一个 entity 或 relation。每个 relation 都必须至少拥有一个 Atom；其 `source` 或 `target` 即使没有出现在 `entities` 中，MAGI 也会自动补成 endpoint-only entity。模型不应为了填满实体数组而编造 Entity Atom；如果实体拥有 aliases、type metadata 或自己的事实，仍应显式提交。没有 Atom 且不是任何关系端点的孤立 entity 仍会被拒绝。插件固定发送 `wait_for_completion=false`；MAGI 接收后返回 HTTP 202、Episode ID 与 track ID，DSH 不等待实体消歧、Atom 裁决、时序演化、embedding 或图/向量投影完成。

同步 API 兼容性保持不变：其他客户端省略 `wait_for_completion` 时仍默认等待完成。后台任务由 MAGI API lifespan 统一追踪，服务关闭时会被纳入 drain；处理失败进入服务端日志和文档状态，不会把已经返回给 DSH 的工具调用重新变成同步失败。

`source_uri` 只表示 provenance，不是幂等键。MAGI 根据相同内容解析 canonical Episode；重复提交会返回既有 Episode，并计入 `skipped_count`。

### Recall

`recall` 默认使用 `mix`，也支持 `local`、`global`、`hybrid` 和 `naive`。它可以传递 `top_k`、`chunk_top_k`、高低层关键词和 `enable_rerank`。query 至少需要三个字符；工具描述同时要求模型把过短实体名扩展成完整问题，例如使用“赵凯是谁”而不是“赵凯”。

返回值是 `/query/data` 的结构化上下文，包括实体、关系、chunks、references 和 retrieval metadata。DSH 模型读取这些材料后自行组织最终答案。

## HTTP client

`MagiClient.request()` 统一处理所有请求：

1. 拼接去掉尾部 `/` 的 `baseUrl`。
2. 按需写入 `Content-Type: application/json` 和 `X-API-Key`。
3. 使用 DSH tool cancellation signal 与 `AbortSignal.timeout(timeoutMs)` 的组合信号。
4. 要求响应是 JSON；非 JSON 响应直接报错。
5. 非 2xx 响应优先读取 `detail`，其次读取 `message`，并封装为带 status/body 的 `MagiHttpError`。

workspace 创建遇到 HTTP 409 时，client 会重新读取 workspace 列表；只有找到精确 ID 或名称匹配项才复用，否则保留原错误。

## 加载与运行

先启动 MAGI Memo，再从 DSH 目录启动 Web profile：

```sh
cd /Users/dengruochen/Desktop/MAGI-Memo/sages/dsh
pnpm dsh web --patch ./magi-memo/cordis.yml
```

然后访问 `http://127.0.0.1:3080`。当前实现是 profile patch，所以每次新启动 DSH 都需要传 `--patch`；除非以后把插件配置合并到所使用的固定 profile。

`cordis.yml` 支持以下环境变量：

```sh
export MAGI_MEMO_BASE_URL=http://127.0.0.1:3491
export MAGI_MEMO_API_KEY=
export MAGI_MEMO_TIMEOUT_MS=30000
export MAGI_MEMO_MAX_OUTPUT_CHARS=30000
export MAGI_MEMO_DEFAULT_MODE=auto
```

修改 `cordis.yml` 或 TypeScript 源码后需要重启 DSH，现有进程不会自动获得新工具注册。

## 验证

在 `sages/dsh` 中运行：

```sh
node node_modules/typescript/bin/tsc -p magi-memo/tsconfig.check.json
node --import tsx --test magi-memo/tests/client.test.ts
node --import tsx scripts/run-oxlint.ts magi-memo
```

client 与 mode 测试覆盖 workspace 创建/复用/激活、API Key、稳定 Episode source、extracted 后台接收参数、recall 参数映射、命令日志折叠和三种 prompt 策略。测试会在 `127.0.0.1` 启动临时 mock server；受限 sandbox 需要允许本机回环监听。

## 扩展一个新工具

增加新能力时保持两层分工：

1. 在 `client.ts` 增加输入类型和一个只负责参数归一化、REST 路径与响应的 method。
2. 在 `index.ts` 用 `defineTool()` 声明模型可见名称、描述、JSON Schema 和 `execute()` 映射。
3. 判断该能力是否允许 agent 主动调用；涉及 workspace 或破坏性操作时，必须在工具描述和 system prompt 中写明用户授权条件。
4. 在 `tests/client.test.ts` 验证请求方法、URL、headers、body、错误与幂等行为。
5. 运行 TypeScript、client test 和 oxlint 三项检查。

不要把 MAGI 的存储逻辑复制到插件中。插件只负责模型工具协议和 HTTP 适配，Episode/Atom 生命周期、workspace 隔离、幂等、Neo4j/NanoVector 投影与删除都由 MAGI Memo 服务端拥有。
