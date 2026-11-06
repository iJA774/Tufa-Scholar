# 网页搜索

## 使用

在设置页的“网页搜索”卡片选择引擎并保存。默认 DuckDuckGo，不需要 API 密钥。右上角搜索窗口的“网页搜索”可以直接查看结果，不调用模型；科研问答的范围选择“网页搜索”会由模型规划查询、检索、取证、补检并核验回答，需要先配置模型服务。

| 引擎 | 配置 | 内容范围 |
|---|---|---|
| DuckDuckGo | 无需密钥 | 通用网页搜索摘要 |
| Tavily | `TAVILY_API_KEY` | 网页搜索及服务返回的正文 |
| InfoQuest | `INFOQUEST_API_KEY` | 网页搜索结果 |
| Brave Search | `BRAVE_SEARCH_API_KEY` | 网页搜索摘要 |
| Serper | `SERPER_API_KEY` | Google 搜索结果摘要 |
| arXiv | 无需密钥 | 论文摘要，不是全文 |
| Searx / SearXNG | `SEARX_HOST`：实例地址 | 实例返回的网页摘要，需启用 JSON 格式 |
| Wikipedia | `WIKIPEDIA_LANG`：语言代码，默认 `zh` | 百科页面文本 |

`SEARCH_API` 指定引擎，取值为 `duckduckgo`、`tavily`、`infoquest`、`brave_search`、`serper`、`arxiv`、`searx`、`wikipedia`。环境变量优先于本机设置，界面显示其来源。密钥分别保存，不因切换引擎复用其他服务的密钥；空白保留已存值，清除只删除本机值。Searx 不附带公共实例，也不自动部署实例。免密钥不代表不受限流、网络或服务策略影响。

DuckDuckGo 使用 `ddgs`，显式指定 DuckDuckGo 后端；该库可能将无法解析的成功响应报告为无结果，界面无法完全区分此情况与真正的空搜索。遇到空结果可更换关键词或搜索引擎。

## 与 DeerFlow 1.x 的关系

参考 [DeerFlow 搜索工具工厂](https://github.com/bytedance/deer-flow/blob/main-1.x/src/tools/search.py) 和 [引擎配置](https://github.com/bytedance/deer-flow/blob/main-1.x/src/config/tools.py)：上游根据 `SEARCH_API` 选一个搜索工具，支持上述八种引擎；上游默认是 Tavily，不意味着所有部署都免密钥。用户无需手填密钥也可能是部署端已配置，或选择了无密钥服务。

上游 [研究节点](https://github.com/bytedance/deer-flow/blob/main-1.x/src/graph/nodes.py) 给研究代理提供搜索与抓取工具；[抓取工具](https://github.com/bytedance/deer-flow/blob/main-1.x/src/tools/crawl.py) 独立负责读取 URL。本项目适配其搜索引擎范围，以已有 LangGraph 有限流程接入，不复制整个代理框架。当前利用搜索服务提供的内容或摘要；没有新增任意 URL 抓取工具，不能把搜索摘要当作已阅读网页全文。

## 数据与执行边界

- `src/integrations/web_search.py` 负责搜索协议、超时、安全错误和统一结果；`src/infrastructure/settings.py` 逐次解析已选引擎配置。工具入口位于 `src/tools/search.py`，网页取证业务位于 `src/literature/evidence.py`，工作流由 `src/agents/literature.py` 编排。
- `GET /projects/{project_id}/web-search?q=...` 返回最多五条结果，验证项目存在，不写项目文献，也不调用模型。
- 提问接口 `source=web` 进入网页证据流程。`all` 仍只代表项目文献与 OpenAlex；收藏夹范围仍禁止外部搜索。
- 一次初始检索，最多两轮补检；每个查询最多五条结果，跨轮候选按 URL 去重与查询排名融合，每轮最多选择六个 URL 取证，每条证据最多 1200 字符。无此前未见的证据时停止补检，不仅比较证据条数。
- 引用保存原始链接、来源引擎、获取时间与内容层级；可用时保留发布时间。获取时间不等同于网页发布时间。网页结果不是经过同行评审的论文，不自动进入论文收藏夹。
- 网页内容作为不可信证据传给模型，不作为指令；保留逐次注入当前时间的中文提示词及既有数值、引用和成本核验。
- Markdown 报告、消息与 CSV 证据表保留来源信息。真实搜索覆盖面、限流、真实模型质量需另行实测；本次使用模拟服务验收。

协议依据和依赖见集成代码；主要接口参考 [Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search)、[Brave](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started)、[SearXNG](https://docs.searxng.org/dev/search_api.html)、[arXiv](https://info.arxiv.org/help/api/user-manual.html)。
