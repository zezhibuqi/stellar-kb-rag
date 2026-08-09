# 账号软删除与密码重置后的 token 失效

`users` 表新增 `is_active` 与 `token_version`：删除账号采用软删除（标记禁用、无法登录、可由管理员恢复启用）；重置密码时 `token_version + 1`，使该用户已签发的 JWT 立即失效。

**Considered Options**：物理删除（会破坏 `documents.uploaded_by` 历史关联且不可恢复）；重置密码后不失效旧 token（存在安全缺口）。

**Consequences**：用户名保持唯一，禁用账号无法同名重建，只能通过“启用”恢复；管理员重置自己的密码后需重新登录。
