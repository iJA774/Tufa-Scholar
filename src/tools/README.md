# 科研工具

工具可通过名称独立调用，无需 LangGraph、任务 ID 或图状态。API 搜索、worker 检索任务与科研问答共用这些入口。

## 发现与调用

在仓库根目录、项目已有的 Python 环境中使用。存储须已初始化，`project_id` 使用实际存在的项目 ID；独立验收应设置隔离的 `TUFA_DATA_DIR`。

```python
from src.tools import ToolContext, ToolRegistry

registry = ToolRegistry()
context = ToolContext(project_id=1, source="local")

# 列出当前来源允许的工具及其参数定义。
catalog = registry.list_tools(context)

result = registry.invoke("select_local_domains", {"domains": ["机器学习"]}, context=context)
if result["documents"]:
    document_id = result["documents"][0]["document_id"]
    evidence = registry.invoke(
        "read_local_markdown", {"document_id": document_id, "query": "attention"}, context=context
    )
```

| 工具 | 参数 | 返回 | 适用来源 |
|---|---|---|---|
| `search_papers` | `query` | 含 `query`、`source`、`method`、`results`、`warnings` 的字典 | `local`、`openalex`、`all`、`collection` |
| `search_web` | `query`、可选 `limit`（1–5，默认 5）、`backend`（`configured` 或 `alternative`，默认 `configured`） | 网页结果列表，含 URL、文本、来源层级、引擎和获取时间 | `web` |
| `read_web_page` | `url`、`limit`（默认 1000，最大 4000） | 清理后的 Markdown 开头、截断状态、最终 URL、标题和获取时间 | `web` |
| `select_local_domains` | `domains`、可选 `offset` | 当前范围的标题、领域、标签、短描述和下一页偏移 | `local`、`all`、`collection` |
| `read_local_markdown` | `document_id`、可选 `query`、可选 `offset` | FTS5 召回的片段及 Markdown 字符定位，或顺序读取的片段 | `local`、`all`、`collection` |
| `collect_evidence` | `query`、`papers`（最多六条检索结果）、可选 `relevance_queries`（最多八条，每条非空且不超过 1000 字符） | 外部论文摘要或网页证据 | `openalex`、联网的 `all`、`web` |

`source="all"` 指项目文献与 OpenAlex，不包含网页搜索。网页调用先创建 `ToolContext(project_id=1, source="web")`，再执行 `search_web`；显式选择 `alternative` 时使用免密钥的 DDGS 备用引擎，结果的 provider 为 `ddgs_fallback`；默认路径仍使用设置中的服务，不自动切换。结果列表可直接作为 `collect_evidence` 的 `papers`。

收藏夹上下文示例：`ToolContext(project_id=1, source="collection", paper_ids=(3, 7))`。调用方使用任务提交时保存的论文 ID 快照，不在执行时重新读取收藏夹当前成员。冻结的上下文复制论文 ID 序列，避免后续修改原列表改变范围。

`search_papers` 的独立收藏夹检索保留完整快照与项目交集。研究图使用 Markdown 目录进行本地研究，并为网络研究维持独立候选池。

目录只列出当前来源可用的工具，执行时再次检查。`project_id`、`source`、`paper_ids` 不属于工具参数，传入会被拒绝。未知工具、不适用的工具及无效参数抛出安全的 `ValueError`；外部服务失败保留集成层的安全错误，混合论文检索保留本地结果及 `warnings`。

## 能力边界

工具上下文由调用方构造，是内部调用契约。工具目录不提供面向 HTTP 的任意工具执行接口。

本地阅读按索引文档 ID 执行，在项目或收藏夹快照内验证范围。在图内，文档 ID 必须先通过领域工具披露。Markdown 检索使用 FTS5 词法匹配，未分词中文支持子串匹配。外部论文摘要和网页片段保留各自来源层级。外部摘要须与 `query` 或 `relevance_queries` 的具体词有词面重合才会生成证据，跨语言可提供对应语言的检索词。

本地文档按片段序号继续读取，证据位置使用规范为 LF 的 Markdown 字符偏移。外部摘要和网页证据保留原有长度限制。PDF 原页缓存继续服务历史报告与文献库。参见 [文献业务说明](../literature/README.md)。

每次 `registry.invoke()` 执行一次工具操作。researcher 通过模型服务的原生 Function Calling 发出调用，代码按当前来源校验参数、限制动作数并管理候选与证据。图层将候选 ID 映射为 `collect_evidence` 的论文对象或 `read_web_page` 的 URL。公开的操作目的会展示在活动面板，工具处理函数只接收业务参数。

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

重复名称被拒绝。`registry.py` 显式接入搜索、外部取证、网页读取和本地 Markdown 工具，算法位于 `src/literature/` 和 `src/integrations/`。新工具应声明适用来源并遵守上下文。提示词位于 `src/prompts/`，模型编排和引用核验由研究工作流负责。

从仓库根目录运行 `uv run --locked python -m unittest tests.test_tools -v` 验证工具入口。跨层回归及实际 API/worker 验收命令见 [根 README](../../README.md#验证)。

`read_web_page` 读取公网 HTTP(S) 网页，返回清理后的 Markdown 开头片段。地址和每次跳转均检查公网 DNS，连接固定已验证 IP，HTTPS 校验原域名。支持 HTML、纯文本和公开 PDF 的可提取文本。PDF 最多提取前 50 页、20 万字符，按页标注，图片型或加密文件不可读取。网页正文片段以 `content_offset` 交给取证时，结果保留绝对字符范围。
