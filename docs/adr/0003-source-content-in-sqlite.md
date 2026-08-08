# 原文档全文存入 SQLite

为支持“查看原文档并定位到引用片段”，`documents` 表新增 `source_content` 列保存完整 Markdown 原文，raw 接口直接从 SQLite 读取；不再保存 `uploads/` 文件副本，也不使用文件路径推断原文档位置。

**Considered Options**：仅存 `file_path` 并按目录约定重建路径（依赖文件系统布局，离线灌库与网页上传两套路径易失配）；保存 `uploads/` 副本 + 路径（冗余，删除时需维护一致性）。

**Consequences**：SQLite 体积随文档增大（可接受，单文档最大约数百 KB）；删除文档只需同步清理 Chroma 向量，无文件清理逻辑；数据迁移与备份以 `app.db` 为准。
