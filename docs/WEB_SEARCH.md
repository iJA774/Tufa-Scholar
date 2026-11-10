# 网页搜索

## 使用

在设置页的“网页搜索”卡片选择引擎并保存。默认 DuckDuckGo，不需要 API 密钥。右上角搜索窗口的“网页搜索”可以直接查看结果，不调用模型；科研问答默认“自动”可由主控选择网页研究；范围选择“网页搜索”会进入网页研究子图，由模型选择检索和取证操作，再经有限补研与报告核验，需要先配置模型服务。

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

DuckDuckGo 显式使用 HTML 搜索后端。HTTP 202 或带验证标记的响应按验证挑战失败处理，不当作成功空结果；正常成功但无结果仍为空。遇到验证挑战可稍后重试或更换搜索引擎。

## 与 DeerFlow 1.x 的关系

参考 [DeerFlow 搜索工具工厂](https://github.com/bytedance/deer-flow/blob/main-1.x/src/tools/search.py) 和 [引擎配置](https://github.com/bytedance/deer-flow/blob/main-1.x/src/config/tools.py)：上游根据 `SEARCH_API` 选一个搜索工具，支持上述八种引擎；上游默认是 Tavily，不意味着所有部署都免密钥。用户无需手填密钥也可能是部署端已配置，或选择了无密钥服务。

上游 [研究节点](https://github.com/bytedance/deer-flow/blob/main-1.x/src/graph/nodes.py) 给研究代理提供搜索与抓取工具；[抓取工具](https://github.com/bytedance/deer-flow/blob/main-1.x/src/tools/crawl.py) 独立负责读取 URL。本项目适配其搜索引擎范围，以 LangGraph 研究子图接入，不复制整个代理框架。当前利用搜索服务提供的内容或摘要；支持读取公开网页的分页正文，但搜索摘要仍不能当作已阅读网页全文。

## 数据与执行边界

- `src/integrations/web_search.py` 负责搜索协议、超时、安全错误和统一结果；`src/infrastructure/settings.py` 逐次解析已选引擎配置。工具入口位于 `src/tools/search.py`，网页取证业务位于 `src/literature/evidence.py`，工作流由 `src/agents/literature.py` 编排。
- `GET /projects/{project_id}/web-search?q=...` 返回最多五条结果，验证项目存在，不写项目文献，也不调用模型。
- 提问接口默认 `source=auto`，主控可选择网页研究；显式 `source=web` 优先网页，`all` 优先本地论文与 OpenAlex；审查发现缺口时可以互相补充，收藏夹范围仍禁止外部搜索。
- 最多三次研究委派，每次最多八次工具动作，主控最多三次决策。科研子图每次搜索最多向研究模型提供三条候选，跨轮候选按 URL 去重与查询排名融合，每次取证最多选择六个 URL，每条证据最多 1200 字符。单轮无新增证据不封锁来源；审查可选择未完成的具体补研计划。模型从累计证据池筛选本轮所需的有界工作集。见 [Agent 工作流](../src/agents/README.md)。
- 引用保存原始链接、来源引擎、获取时间与内容层级；可用时保留发布时间。获取时间不等同于网页发布时间。网页结果不是经过同行评审的论文，不自动进入论文收藏夹。
- 网页内容作为不可信证据传给模型，不作为指令；保留逐次注入当前时间的中文提示词及既有数值、引用和成本核验。
- Markdown 报告、消息与 CSV 证据表保留来源信息。真实搜索覆盖面、限流、真实模型质量需另行实测；本次使用模拟服务验收。

协议依据和依赖见集成代码；主要接口参考 [Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search)、[Brave](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started)、[SearXNG](https://docs.searxng.org/dev/search_api.html)、[arXiv](https://info.arxiv.org/help/api/user-manual.html)。

网页读取按清理后的 Markdown 字符分页；读取前校验公网地址并逐跳检查重定向，HTTPS 固定连接已验证 IP 并校验原域名。HTML 优先提取 article/main，去除导航和脚本，不执行 JavaScript。正文证据带绝对字符区间；动态渲染页面和超限响应可能无法读取。公开 PDF 也可在受限子进程提取可复制文字，下载上限 2 MB，最多 50 页或 20 万字符，截断时有标记；扫描件、加密文件或解析超时不可读取。提取结果作为网页正文的字符定位，不视作已导入论文的 PDF 页码锚点。

网页搜索日志记录安全的引擎、结果数、耗时和错误类别；不记录查询、URL、上游异常正文或密钥。成功空结果与失败分开记录；先前已有成功空结果而后续失败时，研究流程保留结果并提示不完整。`auto` 可转交审查，显式网页从未成功且无证据时返回可重试错误。

2026-11-08 的独立公开查询 `Python official documentation` 曾返回 HTTP 202 和 anomaly-modal 验证标记，DDGS 将该响应报告为无结果；该观察不能反推任务 #12 每次请求的上游响应。适配后同查询复测记录为 `failed`、`challenge`、HTTP 202；DuckDuckGo 仍要求验证，系统不会绕过验证；自动问答可在条件满足时尝试备用检索。

仅 `auto` 问答在搜索受阻或连续两次无候选空结果时可受控尝试 DDGS 的 Bing/Yahoo 备用检索；显式来源和独立搜索不自动切换。遇验证挑战、限流、认证或配置问题时暂停该任务后续搜索；可恢复类型连续失败两次后暂停，成功空结果会重置连续失败计数。暂停仅影响搜索；`auto` 无候选且搜索失败或空结果后即可尝试最多两个不同的已知公开官方 URL，不必等待第二次备用失败；仍可读取已有候选，经同一公网地址检查读取正文后再取证。
