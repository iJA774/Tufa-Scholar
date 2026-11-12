# 网页搜索与读取

## 使用范围

设置页可选择搜索引擎。默认 DuckDuckGo，无需密钥。右上角搜索窗口可独立搜索网页，不调用模型、不写入论文库。科研问答的背景调查和研究轮次可使用网页搜索；`local` 与 `collection` 受项目和提交时收藏夹快照约束，不扩展到网页来源。

| 引擎 | 配置 | 内容范围 |
|---|---|---|
| DuckDuckGo | 无需密钥 | 通用网页搜索摘要 |
| Tavily | `TAVILY_API_KEY` | 搜索结果及服务返回的正文 |
| InfoQuest | `INFOQUEST_API_KEY` | 网页搜索结果 |
| Brave Search | `BRAVE_SEARCH_API_KEY` | 网页搜索摘要 |
| Serper | `SERPER_API_KEY` | Google 搜索结果摘要 |
| arXiv | 无需密钥 | 论文摘要，不是全文 |
| Searx / SearXNG | `SEARX_HOST`：实例地址 | 实例返回的网页摘要，需启用 JSON 格式 |
| Wikipedia | `WIKIPEDIA_LANG`：语言代码，默认 `zh` | 百科页面文本 |

`SEARCH_API` 可指定 `duckduckgo`、`tavily`、`infoquest`、`brave_search`、`serper`、`arxiv`、`searx` 或 `wikipedia`。环境变量优先于本机设置，界面显示其来源。密钥分别保存，切换引擎不会复用其他服务的密钥；空白保留已存值，显式清除才删除本机值。Searx 不附带公共实例，也不自动部署。免密钥不代表不受限流、网络或服务策略影响。

DuckDuckGo 使用 HTML 搜索后端。HTTP 202 或带验证标记的响应按验证挑战失败处理，不当作成功空结果；成功但无结果才是空结果。遇到验证挑战可稍后重试或更换引擎。

## 模块与证据

- `src/integrations/web_search.py` 负责供应商协议、超时、安全错误和统一结果；`src/infrastructure/settings.py` 解析当前引擎配置。
- `src/tools/search.py` 暴露可独立调用的搜索工具；`src/tools/read_web_page.py` 暴露网页正文读取；`src/literature/evidence.py` 负责从结果中提取证据。
- `GET /projects/{project_id}/web-search?q=...` 返回最多五条结果，验证项目存在，不写论文库，也不调用模型。
- 搜索候选、读取正文片段和已取证材料是不同证据层级。报告保留来源 URL、引擎、获取时间及可用的字符区间；获取时间不等同网页发布时间。搜索摘要不等同已阅读的完整网页，网页内容也不自动成为论文收藏夹成员。

网页正文以清理后的 Markdown 分页。读取前检查公网地址并逐跳检查重定向；HTTPS 连接固定到已验证 IP，同时校验原域名。HTML 优先提取 `article`/`main`，移除导航与脚本，不执行 JavaScript。公开 PDF 可在受限子进程提取可复制文字，下载上限 2 MB，最多 50 页或 20 万字符；扫描件、加密文件或解析超时无法读取。正文证据保存绝对字符区间；这不是论文库 PDF 页码锚点。

网页内容传给模型时始终作为不可信数据，不作为指令。搜索日志记录安全的引擎、结果数、耗时和错误类别，不记录查询、URL、响应正文或密钥。读取日志记录结果、耗时、失败类别及可用 HTTP 状态，不暴露网址、正文或原始异常。失败与成功空结果在协议层分开表示。具体工作流轮次与阶段见 [架构说明](ARCHITECTURE.md)。

## 与 DeerFlow 的关系

搜索引擎范围参考 [DeerFlow 搜索工具工厂](https://github.com/bytedance/deer-flow/blob/main-1.x/src/tools/search.py) 和 [引擎配置](https://github.com/bytedance/deer-flow/blob/main-1.x/src/config/tools.py)，但本项目只适配搜索协议和证据读取，不复制其 Agent 调度框架。上游默认 Tavily 不代表本地部署免密钥；可用引擎与实际内容层级见上表。

协议依据和依赖见集成代码；主要接口参考 [Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search)、[Brave](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started)、[SearXNG](https://docs.searxng.org/dev/search_api.html) 和 [arXiv](https://info.arxiv.org/help/api/user-manual.html)。
