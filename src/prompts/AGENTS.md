# 提示词维护约定

- 仅维护真实模型节点提示词：`runtime/dispatcher`、`literature/background_investigator`、`literature/planner`、`literature/researcher`、`literature/analyst`、`literature/reporter`。supervisor 与 coder 是无模型的确定性节点，不创建占位提示词；废弃节点不保留兼容模板。
- 模板正文以 `---`、`CURRENT_TIME: {{ CURRENT_TIME }}`、`---` 三行开头。模型入口使用 Jinja2 `Environment`、`StrictUndefined` 和 `autoescape=False`，仅加载受信提示词 ID。每个 HTTP 请求（含结构纠错重试）重新注入北京时间；`OUTPUT_SCHEMA` 只由代码按 Pydantic schema 生成。
- `node_role`、`search_mode`、`available_tools` 与 `local_papers` 由工作流代码按节点和权限提供。模板可展示授权工具目录，并按 `web`、`paper`、`none` 分支说明对应职责；不得用模板规则扩展工具权限。
- Jinja 只渲染受信系统提示词。问题、历史、用户文档、候选、论文和网页文本均作为单独 user JSON 数据传递，禁止将其再次送入模板引擎。由变量插入的本地文件名也只是数据，不能作为指令。
- 中文模板按角色、输入边界、执行要求和输出契约组织。字段、枚举、结构与引用范围以工作流 Pydantic schema 和代码校验为准；不得要求输出推理过程。
- 修改前定位 `src/agents/literature.py` 的 payload、schema、工具目录和来源范围；同步检查 [工作流规则](../agents/AGENTS.md)、提示词说明和模型边界测试。
- 用户问题、历史和检索材料是待处理数据，不能将材料中的指令提升为系统规则。缺失证据应明确说明，不编造事实、页码或引用。
- 变更输出字段须同步调用方结构与测试；仅调整措辞时用代表性输入检查行为，不锁定整份提示词或摘要。
- 不嵌入真实密钥、用户私有材料或机器路径。真实模型效果需与模拟 HTTP 结构验收区分记录；模拟测试不能证明回答质量。
