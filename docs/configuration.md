# 环境配置

配置加载顺序为：进程环境变量优先，其次是项目根目录 `.env`。模板见 [../.env.example](../.env.example)。

| 分组 | 变量 | 作用 |
| --- | --- | --- |
| LLM | `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`、`LLM_TEMPERATURE`、`LLM_TIMEOUT`、`LLM_MAX_RETRIES` | OpenAI 兼容模型接入 |
| 执行层 | `QUERY_TIMEOUT_MS`、`MAX_SCAN_ROWS`、`MAX_RESULT_ROWS`、`SQL_SELF_HEAL_MAX_RETRIES` | 超时、扫描熔断、结果上限与自愈 |
| 澄清 | `CLARIFY_SLOT_TTL` | 多轮澄清槽位上下文 TTL（秒） |
| 审计 | `AUDIT_ENABLED`、`LOG_LEVEL` | 审计开关与日志级别 |
| 认证 | `AUTH_ENABLED`、`AUTH_STRICT`、`WEB_HOST` | HTTP 鉴权与服务绑定 |
| 默认身份 | `AUTH_DEFAULT_PRINCIPAL`、`AUTH_DEFAULT_USER`、`AUTH_DEFAULT_DISPLAY` | 鉴权关闭时的服务端默认身份 |
| JWT | `AUTH_JWT_SECRET`、`AUTH_JWT_ISSUER`、`AUTH_JWT_AUDIENCE`、`AUTH_JWT_TTL` | JWT 签名与有效期 |
| Session | `AUTH_SESSION_TTL`、`AUTH_SESSION_DB` | 会话有效期与可选 SQLite 共享存储 |
| 限流 | `AUTH_LOGIN_MAX_FAILURES`、`AUTH_LOGIN_BASE_SECONDS`、`AUTH_LOGIN_MAX_SECONDS` | 登录失败指数退避 |

## 生产注意事项

- 生产环境必须替换 `AUTH_JWT_SECRET`。
- `AUTH_STRICT=1` 或绑定非 localhost 地址时，启动会拒绝弱默认密钥与关闭鉴权。
- `AUTH_ENABLED=0` 也不会信任客户端传入的 principal，服务端仍使用 `AUTH_DEFAULT_*`。
