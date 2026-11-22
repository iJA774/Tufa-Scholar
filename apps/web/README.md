# Web 开始页与研究工作区

`app/page.tsx` 是独立开始页入口，只装配 `launch/LaunchPage.tsx`；LED Canvas 动画和样式单独封装，不请求 API、不装载研究状态。“开始使用”进入 `/workspace`，GitHub 链接在新标签页打开仓库。

`app/workspace/page.tsx` 是研究工作区入口，装配 `workspace/WorkspacePage.tsx`。光标悬浮在左上角标志时展开历史抽屉，移出标志和抽屉后收起。工作区通过 `GET /conversations` 加载跨项目历史，打开会话时根据 `project_id` 切换项目。历史项菜单支持置顶、解除置顶和确认删除，置顶状态由本机 API 保存。项目仍限定可用 PDF、文献与新研究范围。

工作区提供平铺历史导航和设置弹窗。提问框的模式菜单提供“自动”“深度研究”和“快速探索”，每次明确提交 `research_mode: auto|deep|quick`。首次默认使用 auto，由 dispatcher 选择流程；已有浏览器偏好会继续生效。deep 强制进入研究流程，quick 强制直接回答并跳过背景调查与检索。“研究偏好”的深度、原始证据优先和相关性过滤随新问题提交。回答风格提供学术综述（默认）、探索发现、决策简报和通俗解读，随新问题提交并用于直接回答和研究终稿。重试和重新回答沿用原任务的模式、偏好和回答风格。

提问框提供“网络搜索”和“本地文献”按钮，点亮表示启用，按钮的 `aria-pressed` 反映同一状态。提交问题时发送 `network_search` 和 `local_literature`。

设置按钮和 `/settings` 都在工作区上方打开 `SettingsPanel`。“本地文档”的“打开目录”通过本机 API 在资源管理器中打开固定原文目录。“预处理”先转换 Markdown，再分类并为提交时的项目建立检索索引。批次显示进度与分类结果，支持取消、预览和下载。最近批次 ID 保存在浏览器，关闭设置后批次继续运行。“通用”的文档分类模型留空时使用主模型。关于入口在新标签页打开 GitHub。所有业务数据请求通过本机 `/api` 代理。

深度研究使用 DeerFlow 风格的报告与活动面板。桌面聊天列展开为 538px、收起为 768px，面板按视口宽度计算并随两列间距动画展开；窄屏使用居中的可滚动面板。活动内容呈现本地任务阶段、子任务、发现和真实来源，跟随新增活动滚动。报告以 Markdown 渲染并可下载。“细节”按节点展开模型输入输出和工具检索结果，正文在展开时按需读取。进行中的深度任务切换历史后自动打开，已完成任务可从聊天重新打开；快速探索不自动显示面板。

研究面板的布局、活动动效、卡片和 tabs 参考并改写自 ByteDance DeerFlow。完整 MIT 许可文本保存在 [DEERFLOW-MIT-LICENSE.txt](app/workspace/THIRD_PARTY_LICENSES/DEERFLOW-MIT-LICENSE.txt)，移植组件在源码头部标注出处。

| 模块 | 职责 |
|---|---|
| `app/page.tsx`、`launch/LaunchPage.tsx`、`launch/LaunchPage.module.css` | 独立开始页路由、内容与 LED Canvas 动画 |
| `app/workspace/page.tsx`、`workspace/WorkspacePage.tsx`、`WorkspacePage.module.css` | 工作区路由、项目范围、单层历史抽屉、设置弹窗及其深色样式 |
| `workspace/ResearchChat.tsx`、`ResearchChat.module.css` | 当前会话消息、deep/quick 模式、聊天列宽度动画、轮询、重试、证据和下载 |
| `workspace/ResearchPanel.tsx`、`ResearchPanel.module.css` | DeerFlow 风格报告/活动面板、真实研究活动和 Markdown 报告展示 |
| `app/settings/SettingsPanel.tsx`、`SettingsPanel.module.css`、`app/settings/page.tsx` | 设置弹窗、直接访问入口、服务配置和本地文档 |
| `workspace/PdfImport.tsx`、`workspace/PaperLibrary.tsx`、`workspace/PaperRow.tsx` | 当前项目 PDF 导入、刷新、列表和原文访问 |
| `app/settings/WebSearchSettings.tsx`、`RuntimeLogsSettings.tsx` | 网页搜索设置及本地运行日志管理 |
| `app/settings/LocalDocuments.tsx`、`LocalDocuments.module.css` | 打开原文目录、开始转换、批次轮询、预览与下载 |
| `workspace/api.ts`、`workspace/types.ts`、`workspace/sourceLabels.ts` | 工作区请求辅助、API 数据类型及来源标签 |
| `workspace/preferences.ts` | 研究模式、研究偏好、Enter 发送、外观选项和模型选择的本地工作区偏好 |
| `app/hooks/useRunPolling.ts`、`app/hooks/runPolling.ts` | 任务轮询的 React 生命周期适配与独立调度逻辑 |
| `app/ModelControls.tsx` | 提问模型 ID 手动输入与环境变量锁定 |

切换项目、会话、任务或卸载时取消旧请求，防止过时结果更新当前界面。

历史对话中的原问题下方提供“重新回答”。它使用当前模型设置创建新的问答任务，保留原问题及研究范围；旧回答和报告不覆盖，也不重复添加用户消息。会话内仍有任务进行时禁用此入口，提交后继续显示新任务的进度与报告。失败任务的“重新执行”继续使用原任务模型快照。

支持固定证据的报告提供“固定证据重新生成”，可选回答风格并手动填写模型 ID；留空使用原报告模型。新版本追加到对话，每版可下载或通过“查看报告”在研究面板中打开。任务进行时不能再次提交。

## 验收

从仓库根目录运行：

```powershell
pnpm check:web
pnpm build:web
pnpm --dir apps/web exec tsc app/hooks/runPolling.ts --target es2020 --module commonjs --outDir ../../.cache/polling-test --skipLibCheck
node apps/web/app/hooks/runPolling.test.cjs (Resolve-Path .cache/polling-test/runPolling.js)
pnpm --dir apps/web exec tsc app/hooks/runEvents.ts --target es2020 --module commonjs --outDir ../../.cache/events-test --skipLibCheck
node apps/web/app/hooks/runEvents.test.cjs (Resolve-Path .cache/events-test/hooks/runEvents.js)
pnpm --dir apps/web exec tsc app/workspace/reportDisplay.ts --target es2020 --module commonjs --outDir ../../.cache/report-display-test --skipLibCheck
node apps/web/app/workspace/reportDisplay.test.cjs (Resolve-Path .cache/report-display-test/reportDisplay.js)
```

桌面交互验收可以使用带真实 API/SQLite、模拟模型和搜索、但不启动 worker 的浏览器 fixture：

```powershell
uv run --locked python -m scripts.fixture_api --data-dir .cache/desktop-research-fixture --port 18180 --local-config
```

fixture 要求新建或空的数据目录。`--local-config` 会清除 fixture 进程继承的服务密钥环境变量，并在该隔离 SQLite 中保存假模型和 Searx 设置；不会调用真实服务。输出中给出可上传样例 PDF 的路径。待处理任务需对 `POST /__fixture/advance` 调用两次，失败任务可重试；quick 不保存证据，deep 显示模拟 researcher 阶段。此 fixture 仅用于浏览器交互和页面内容，不用于验证真实 graph、worker、模型或检索闭环。实际验收范围见 [开发指南](../../docs/DEVELOPMENT.md#验证)。
