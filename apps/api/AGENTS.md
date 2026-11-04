# API 约定

API 使用根目录 uv 环境，与单 worker 共用 `TUFA_DATA_DIR` 和 SQLite。导入 PDF 的解析在数据库事务外进行；无可提取文本时返回 `no_extractable_text`，不执行 OCR。文件以内容摘要去重，同一 DOI 或 arXiv 文献的新文件保留独立版本。项目内文献由各对话共享；检索候选仅在用户选择保存后进入项目。任务失败或中断后重试创建新任务并保留原记录。

设置保存在 `TUFA_DATA_DIR` 的 SQLite；界面提交的 OpenAlex 和 OpenAI 兼容模型密钥为用户明确要求的本地配置例外。`OPENALEX_API_KEY`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL` 环境变量优先；接口只返回密钥是否配置及来源，不返回密钥。OpenAlex 每次请求读取设置；模型配置目前只保存。删除项目的短事务禁止删除运行中任务所属项目，保留共享文献和原文。
