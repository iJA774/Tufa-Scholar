# API 约定

API 使用根目录 uv 环境，与单 worker 共用 `TUFA_DATA_DIR` 和 SQLite。导入 PDF 的解析在数据库事务外进行；无可提取文本时返回 `no_extractable_text`，不执行 OCR。文件以内容摘要去重，同一 DOI 或 arXiv 文献的新文件保留独立版本。项目内文献由各对话共享；检索候选仅在用户选择保存后进入项目。任务失败或中断后重试创建新任务并保留原记录。
