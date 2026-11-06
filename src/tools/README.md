# 科研工具

工具可通过名称独立调用，无需 LangGraph、任务 ID 或图状态。API 搜索、worker 检索任务与科研问答共用这些入口。

## 发现与调用

在仓库根目录、项目已有的 Python 环境中使用。存储须已初始化，`project_id` 使用实际存在的项目 ID；独立验收应设置隔离的 `TUFA_DATA_DIR`。

```python
from src.tools import ToolContext, ToolRegistry

registry = ToolRegistry()
context = ToolContext(project_id=1, source="local")

# 名称、中文说明及 JSON Schema，可供调用方展示或生成调用参数。
catalog = registry.list_tools(context)

result = registry.invoke(
    "search_papers", {"query": "attention"}, context=context
)
evidence = registry.invoke(
    "collect_evidence",
    {"query": "attention", "papers": result["results"][:6]},
    context=context,
)
```

| 工具 | 参数 | 返回 | 适用来源 |
|---|---|---|---|
| `search_papers` | `query` | 含 `query`、`source`、`method`、`results`、`warnings` 的字典 | `local`、`openalex`、`all`、`collection` |
| `search_web` | `query`、可选 `limit`（1–5，默认 5） | 网页结果列表，含 URL、文本、来源层级、引擎和获取时间 | `web` |
| `collect_evidence` | `query`、`papers`（最多六条检索结果） | 带原文定位或网页出处的证据列表 | 全部来源 |

`source="all"` 指项目文献与 OpenAlex，不包含网页搜索。网页调用先创建 `ToolContext(project_id=1, source="web")`，再执行 `search_web`；结果列表可直接作为 `collect_evidence` 的 `papers`。

收藏夹上下文示例：`ToolContext(project_id=1, source="collection", paper_ids=(3, 7))`。调用方使用任务提交时保存的论文 ID 快照，不在执行时重新读取收藏夹当前成员。冻结的上下文复制论文 ID 序列，避免后续修改原列表改变范围。

目录只列出当前来源可用的工具，执行时再次检查。`project_id`、`source`、`paper_ids` 不属于工具参数，传入会被拒绝。未知工具、不适用的工具及无效参数抛出安全的 `ValueError`；外部服务失败保留集成层的安全错误，混合论文检索保留本地结果及 `warnings`。

## 能力边界

工具上下文由应用构造，是内部调用契约。本轮未增加面向 HTTP 的任意工具执行接口。

本地与收藏夹取证从项目仓储读取正文路径、版本与元数据，传入候选不能指定任意本机文件。收藏夹候选还必须属于快照。外部论文摘要和网页片段保留各自来源层级，网页不自动进入论文库。

每次取证最多处理六条候选。论文检查每篇首个可提取版本的前 20 页，按问题词选择最多三页，每页最多 1200 字符；摘要和网页片段也最多 1200 字符。取证与完整阅读之间的限制继续见 [科研流程说明](../literature/README.md)。

工具只执行一次操作。LangGraph 仍负责规划、最多两轮补检、综合和核验，worker 与仓储负责阶段及结果保存。本轮没有模型自主选工具的循环，也没有自动扫描工具文件或加载第三方插件。

## 扩展

为新能力声明 Pydantic 输入模型，再在使用的 registry 实例上显式注册。处理函数签名为 `handler(params, context)`，返回可由调用方使用的数据：

```python
from pydantic import BaseModel, ConfigDict, Field

class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, description="待处理的文本")

def text_length(params, context):
    return {"length": len(params.text)}

registry.register(
    "text_length", "计算文本长度。", Input, text_length,
    sources={"local"},
)
registry.invoke("text_length", {"text": "科研"}, context=context)
```

重复名称被拒绝。内置能力在 `research.py` 中集中显式注册；新工具应声明实际适用来源，并在执行中遵守上下文。提示词和报告核验留在对应模块，不塞入注册器。

从仓库根目录运行 `uv run --locked python -m unittest tests.test_tools -v` 验证工具入口。跨层回归及实际 API/worker 验收命令见 [根 README](../../README.md#验证)。
