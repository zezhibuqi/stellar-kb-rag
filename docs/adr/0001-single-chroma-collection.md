# 单一 Chroma Collection + metadata 过滤

系统所有知识领域的向量统一存放在一个 Chroma Collection 中，通过每条向量 metadata 里的 `domain` 字段区分领域，检索时用 `where={"domain": {"$in": allowed_domains}}` 做权限过滤；删除文档时按 `doc_id` 精确清理向量。

**Considered Options**：按领域各建一个 Collection（隔离更彻底，但需维护 5 个集合、权限检索需跨集合聚合）；按角色各建集合（权限查询简单，但数据冗余且角色变化需重建）。

**Consequences**：单集合下所有写入和查询共享同一资源，需要保证 `domain` 值与 SQLite `domains.name` 完全一致；`doc_id` 是跨 Chroma 与 SQLite 的同步锚点，删除必须双写清理。
