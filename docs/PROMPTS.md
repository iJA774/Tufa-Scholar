# 中文提示词规范

## 参考与适配

参考 [DeerFlow main-1.x 提示词目录](https://github.com/bytedance/deer-flow/tree/main-1.x/src/prompts)、[中文规划模板](https://github.com/bytedance/deer-flow/blob/main-1.x/src/prompts/planner.zh_CN.md) 和 [模板加载代码](https://github.com/bytedance/deer-flow/blob/main-1.x/src/prompts/template.py) 的开头时间变量、职责分工和结构化约束方式。本项目借鉴其将硬输出契约与 JSON 格式说明分开表达的方式，按实际节点编写，不复制其工具、多代理调度或强制联网要求。科研提示词描述角色与任务，JSON Schema 规定结构；具体工具参数、约束和动作示例放在相应工具 docstring，并通过工具目录传给模型。

## 模板与真实调用

| prompt ID | 输入 | 输出结构 |
|---|---|---|
| `runtime/route` | question、history、source、effective_source、available_actions、results、evidence、review、delegations_remaining、decisions_remaining、directions、verification_gaps | Route：action、task、answer、directions |
| `literature/research` | role、source、task、tools、candidates、candidate_count、recent_result、evidence、calls_remaining、directions、plan、review、results、web_blocks、web_read_failures、verification_gaps、search_status、search_blocked、direct_url_attempts_remaining、search_backend、fallback_reason、empty_searches、prior_subtask_notes、can_read_direct_url、direct_url_reason、attempted_direct_urls | Specialist：action、query、candidate_ids、url、plan、offset、limit |
| `literature/review` | question、directions、evidence、results、allowed_routes、attempted_plans、search_blocked、search_backend、fallback_reason、empty_searches、current_task、current_source、prior_subtask_notes | Review：sufficient、gaps、summary、coverage、next_steps、irrelevant_evidence_ids、subtask_findings、subtask_gap |
| `literature/synthesize` | question、history、directions、evidence、review、verification_gaps | Draft：sections（heading、paragraphs：text、citations）、limitations |
| `literature/verify_claim` | heading、claim、evidence | Verdict：status、reason、suggestion、research_query、research_route |
| `literature/revise` | question、draft、feedback、evidence、verification_gaps | PatchSet：patches（section_index、paragraph_index、text、citations） |

`src/agents/literature.py` 调用 `src/integrations/model.py` 的 `structured()`。该入口从 `src/prompts/` 按 prompt ID 读取 Markdown（如 `literature/research` 对应 `src/prompts/literature/research.md`），逐次替换时间变量，将渲染后的全文放进 system 消息，输入 JSON 单独放进 user 消息，发送至配置的 `/chat/completions`，在 system 消息中附加调用方的 Pydantic JSON Schema，保持 `json_object` 响应格式，并校验响应。Draft 兼容丢弃模型误回显的输入字段 `verification_gaps`，成对且有效的顶层 `heading` 和 `paragraphs` 归入章节；其他额外字段仍拒绝。草稿允许空引用，逐段核验时剔除无证据内容。两次结构错误仍无法恢复时输出已有资料清单与失败说明，报告阶段的类型化模型格式或服务故障保留可用资料与已核验段落，输出阶段性结果；其他异常不作为模型故障吞掉。结构错误最多附带安全的错误类型和字段路径重试一次；日志与反馈不包含原始模型输出。模型结构校验反馈仅含安全错误类型（如 `extra_forbidden`、`value_error`）和字段路径，未知字段名以 `*` 隐去，数组下标保留。研究动作的语义纠错区分 `repeated_direct_url`、`conflicting_candidate_url` 等安全代码，不把原始材料放入反馈；同一已知候选同时附上匹配 URL 时，代码归一化为候选读取。工具约束与合法调用案例见 [网页读取工具 docstring](../src/tools/read_web_page.py)。加载路径基于模块文件位置，不依赖当前工作目录。

所有提示词正文的前三行固定为：

```text
---
CURRENT_TIME: {{ CURRENT_TIME }}
---
```

时间由代码在每次模型请求前生成，采用北京时间固定 UTC+08:00，ISO 8601 格式并包含秒和时区偏移；不依赖操作系统时区，不在进程启动时缓存。仅替换这个保留变量，不对 JSON 花括号或用户输入做模板求值，因此无需模板引擎依赖。时间用于理解相对日期，不证明证据具有时效性。

## 撰写要求

1. 明确一个节点的职责和非职责，使用中文角色、输入、输入边界、执行要求与输出契约；保留英文 API 字段、证据 ID 与必要专业术语。
2. 输入字段只写调用方真实传入的内容；不发明工具、文献全文、来源筛选或未接入的修订能力。
3. 用户问题决定任务，历史与外部文本只是数据；材料中的指令不得更改系统规则、输出格式或授权范围。
4. 输出只含约定 JSON，不带代码围栏、额外字段或推理过程；示例须为合法 JSON，并包含缺证据或停止路径。
5. 结论逐条关联真实证据 ID；区分摘要和正文片段，核对数值、单位、指标与条件。证据不足要停止、补检或说明局限，不编造事实。
6. 确定性校验、来源隔离、循环上限与最终引用排版由代码负责，不能将提示词约束视为安全或事实保证。
7. 新增模板必须同时接入调用方和输出结构；未实现节点不添加占位正文。工具调用限制与示例由工具 docstring 维护，避免在角色提示词重复。

网页证据须区分 `web_snippet`（搜索摘要）、`web_content`（网页文本）和 `abstract`（论文摘要）。链接、获取时间、提供方只用于追溯，不证明内容正确或经过同行评审；网页中的操作要求和提示词均作为不可信数据处理。选择 `web` 时由代码强制进入网页研究子图；`auto` 首次路由可选择直接回答、论文或网页，审查缺口后可在允许范围内切换论文与网页，保留逐次注入当前时间的首部。

## 验收方式

用模拟 HTTP 响应调用实际 `structured()`，检查发出的 system 首部时间、逐次更新时间、用户材料隔离及各输出结构兼容；保留工作流的范围、证据不足和核验拒绝回归。不要锁定整份提示词或固定摘要。

模拟验证不证明真实模型的遵循程度、中文质量或事实可靠性。真实模型试验需单独记录服务、输入和结果；本次由用户稍后自行验证，不启动真实业务链路。
