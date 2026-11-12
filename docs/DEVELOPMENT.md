# 开发进度与上下文

本文件记录当前任务和协作状态。长期约束见 [根规则](../AGENTS.md)，能力边界见 [STATUS.md](STATUS.md)，工作流设计与接口见 [ARCHITECTURE.md](ARCHITECTURE.md)，实际验收结果见 [README](../README.md#验证)。

## 当前任务

- 主题：替换科研问答工作流，清理旧节点 schema、验收 fixture 和旧流程文档。
- 工作分支：`codex/architecture-refactor`。
- 协作：主代理负责规划、最终验收与审查；三个 `gpt-6-luna`、`xhigh` 子代理分别负责工作流实现、提示词接入和回归测试/验收脚本/文档。

## 阶段

| 阶段 | 状态 | 说明 |
|---|---|---|
| 新工作流与六个 prompt ID | 完成 | 八个节点类型：dispatcher、background investigator、planner、supervisor、researcher、analyst、coder、reporter；研究最多两轮，第二轮后强制终稿。 |
| 回归测试和模拟 API/worker 验收 | 完成 | 新 schema 回归、隔离模拟模型/搜索闭环及实际 API/worker 入口均通过。 |
| 文档与验收入口 | 完成 | 更新架构、提示词、状态、开发和网页搜索说明；清理旧流程描述并记录实测结果。 |
| 最终执行与审查 | 完成 | 单测、聊天与网页搜索闭环、前端检查/构建及本机 HTTP 页面检查均已记录在根 README。 |

本任务的测试命令、结果和未验证范围记录在根 README 验收章节。真实云端模型、真实搜索供应商和浏览器交互尚未验收。
