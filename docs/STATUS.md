# 项目状态

本文只记录可用能力、限制和验证边界。设计流程见 [ARCHITECTURE.md](ARCHITECTURE.md)，当前开发任务见 [DEVELOPMENT.md](DEVELOPMENT.md)，安装与最新实测记录见 [README](../README.md)。

## 已实现的基础能力

- 项目、对话、任务、文献、收藏夹、Markdown 报告和 CSV 证据表使用本机 SQLite；worker 单进程顺序执行任务，支持失败、中断和显式重试。
- 本地 FTS5/BM25 检索、OpenAlex 外部论文检索及混合排名；支持 PDF 上传、同文件去重、原文版本和按页保存的文本证据。
- `src/tools/` 提供论文检索、网页搜索、网页读取和证据提取的显式工具目录。工具可独立执行，项目、来源和收藏夹范围由上下文固定。
- 网页搜索支持 DuckDuckGo、Tavily、InfoQuest、Brave、Serper、arXiv、Searx 和 Wikipedia。公开网页与 PDF 读取包含公网地址校验、重定向检查、分页提取和错误脱敏；详见 [WEB_SEARCH.md](WEB_SEARCH.md)。
- 本机设置、环境变量优先、模型发现与模型/思考强度选择；接口和运行日志不回显密钥。前端研究工作台与设置页、单机启动脚本和任务进度轮询已存在。

## 当前工作流能力与限制

当前实现流程为 `dispatcher → background_investigator → planner → supervisor`，每轮 supervisor 依次调度 `researcher → analyst → coder → planner`，最多两轮后强制 `reporter` 成稿；简单直接回答由 dispatcher 结束，不调用工具。隔离回归、模拟模型/搜索 HTTP 闭环和实际 API/worker 入口均已通过；节点和数据契约见 [ARCHITECTURE.md](ARCHITECTURE.md) 与 [PROMPTS.md](PROMPTS.md)，实测命令及结果见 [README 验证](../README.md#验证)。

收藏夹任务使用提交时的项目内文献快照，不读取其他对话历史，不扩大到收藏夹外来源；空集合应返回证据不足。论文和网页来源、候选池及累计证据由代码维持边界。报告引用与数值检查仅对传入证据有效，不能保证模型结论完全正确，仍需核对原文。

## 尚未实现或未验证

- 模拟服务不能证明真实云端模型的 JSON 兼容性、权限、响应质量和耗时。真实搜索供应商的当前可用性、权限、限流和结果质量也未由本轮验收覆盖。
- 未执行真实浏览器交互；前端类型检查、构建和本机 HTTP `/`、`/settings` 响应通过不代表浏览器端到端行为已验证。
- 没有向量检索、模型语义重排、OCR、引用扩展或历史报告修订版本链。检索排名是词项与来源排序启发式，不保证语义相关性最优。
- 图片型、加密或无法提取文本的 PDF 不能提供按页证据；动态渲染网页可能无法读取。网页搜索摘要和截取片段不等同全文。

隔离单测、模拟 HTTP、实际 API/worker、真实外部服务及浏览器交互分别记录。一个层级通过不代表其他层级已验证；本轮实际命令和未覆盖范围见 [README 验收部分](../README.md#验证)。
