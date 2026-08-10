# MAGI Memory Core：当前阶段使用方式

当前阶段对外提供 Runtime 生命周期和主要业务操作：

```text
init → open → ingest/search/status → close → finalize
```

外部接口组合一个完整 `magi_core.MagiCore` 实例、一个随核心初始化的 Neo4j graph storage，以及位于同一 `ragstore`
中的 `magi-memory.db`。`.db` 只是更直观的文件后缀，底层仍是标准 SQLite。Neo4j 是外部服务，不会在本地 workspace 中创建数据库目录。

## 代码调用

调用者仍然按照现有 LightRAG 方式配置模型和存储，只需确保构造函数收到回调给出的 `ragstore` 和 `workspace_id`：

```python
import asyncio

from magi_core import MagiCore as MagiEngine
from interface import MagiAPI


def build_engine(ragstore, workspace_id):
    return MagiEngine(
        working_dir=str(ragstore),
        workspace=workspace_id,
        llm_model_func=my_llm_model,
        embedding_func=my_embedding_model,
        graph_storage="Neo4JStorage",
        # 其余兼容 storage 与 provider 配置保持不变
    )


async def main():
    api = MagiAPI.build(
        workspace="./workspaces/personal-memory",
        core_factory=build_engine,
    )

    await api.init()
    handle = await api.open(owner="example-agent")
    try:
        await handle.ingest_file("./episodes/today.txt")
        answer = await handle.search("今天讨论了什么？", mode="mix")
        print(answer)
    finally:
        await handle.close()
        await api.finalize()


asyncio.run(main())
```

也可以直接提交内存中的 Episode：

```python
from datetime import datetime, timezone

from magi_core import Episode, EpisodeKind


episode = Episode(
    id="conversation-2026-08-05-001",
    kind=EpisodeKind.CONVERSATION,
    content="用户与助手本轮经过过滤后的对话。",
    reference_at=datetime.now(timezone.utc),
    source_uri="conversation://2026-08-05/001",
)

await handle.ingest(episode)
```

文件只是 Episode 输入载体。启用 `MagiAPI` 时，同一次抽取调用会直接输出实体、关系及其 Atom 和双时间字段；
之后由严格提交 adapter 完成候选召回、批量实体消歧和图投影。当前 Atom 去重策略关闭，Atom 及实体/关系描述均按提取顺序保留。

## 工作区产物

```text
mgc-test/
├── inputs/                 # WebUI 上传和 Episode 文件入口
├── logs/
├── artifacts/             # 手工/在线验收产物
└── ragstore/
    ├── magi-memory.db      # Episode、Atom、Evidence 和演化记录
    └── magi_memo_dev/
        └── *.json          # MAGI Core 的 KV、向量、缓存与状态
```

WebUI 已迁移到 `src/webui`，并继续使用核心中保留的 API 服务。服务启动时会在
`WORKING_DIR/magi-memory.db` 初始化同一个 Episode/Atom 记录层并注册严格 adapter；
上传文件完成解析后、进入抽取调用前会建立 Episode，处理成功或失败时同步 Episode 状态。
WebUI 首页已经改为 MAGI Memo 的记忆工作台：可查看 SQLite 中的 Episode、Atom、Evidence、实体与关系统计，
可展开 Episode → Atom 和 Atom → Evidence 的双向来源信息；原上传、扫描和处理队列保留在“写入与队列”页，
原知识图谱与检索页继续使用。

## 启动 WebUI

仓库自带的生产 WebUI 已构建进 Python 包，由同一个 FastAPI 服务提供。根目录 `.env` 已配置为：

```dotenv
HOST=127.0.0.1
PORT=9621
INPUT_DIR=./mgc-test/inputs
WORKING_DIR=./mgc-test/ragstore
LOG_DIR=./mgc-test/logs
WORKSPACE=magi_memo_dev
```

启动：

```bash
./server
```

`./scripts/run-webui.sh` 仍保留为兼容入口，并会转发到同一个 `server` 可执行文件。

然后访问 `http://127.0.0.1:9621/webui/`。健康检查和 OpenAPI 文档分别位于
`http://127.0.0.1:9621/health`、`http://127.0.0.1:9621/docs`。

首次拉取或修改了 `src/webui` 后，安装依赖并重新构建静态包：

```bash
cd src/webui
bun install --frozen-lockfile
bun run build:bun
```

当前 `mgc-test/inputs`、`mgc-test/ragstore` 与 Neo4j 的 `magi_memo_dev` 空间已重置，适合逐条验收新写入链路；
历史验收数据只保留在 `mgc-test/artifacts`，不会参与当前服务。服务启动后会创建一个全新的
`magi-memory.db` 以及空的 KV、向量、状态存储。
