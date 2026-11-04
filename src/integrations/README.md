# 外部集成

`openalex.py` 使用 OpenAlex Works 的文本检索并保留元数据及摘要来源层级。API 支持无密钥试用；如需使用自己的额度，设置环境变量 `OPENALEX_API_KEY`。请求失败会标记任务失败，本地检索仍可独立使用。参考 [OpenAlex API](https://help.openalex.org/api/) 和 [搜索说明](https://help.openalex.org/api/searching/)。
