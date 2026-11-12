# 运行时提示词

`dispatcher.md`（ID：`runtime/dispatcher`）判断当前请求是否需要深度研究。无需深研时直接返回答案；需要深研时交给工作流继续处理。collection 的强制研究规则由代码执行。

`structured_output.md` 是六个模型提示词共同 include 的 JSON Schema 与格式纠错契约，不是独立模型节点，也没有 prompt ID。schema 由 `structured()` 按实际 Pydantic 类型生成。

工作流分支、工具范围和提示词上下文见 [科研提示词说明](../literature/README.md)、[提示词规则](../AGENTS.md) 与 [提示词规范](../../../docs/PROMPTS.md)。
