# Web UI 与 API

## 启动服务

```bash
python -m web.server 8000
```

默认地址：`http://127.0.0.1:8000`。

## 端点

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/api/health` | GET | 健康检查 |
| `/api/metrics` | GET | QPS、分位数、意图/动作分布等进程内指标 |
| `/api/auth/login` | POST | 用户名/口令换取 JWT 与 Session |
| `/api/auth/logout` | POST | 吊销服务端 Session |
| `/api/auth/me` | GET | 返回当前身份 |
| `/api/query` | POST | 执行受保护的数据查询 |
| `/static/` | GET | 单页 Web 前端 |

## 查询示例

登录：

```bash
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"analyst","password":"analyst123"}'
```

查询：

```bash
curl -X POST http://127.0.0.1:8000/api/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"query":"各品类成功订单的GMV分布？"}'
```

`/api/query` 的响应包含 DSL、SQL、列、行、解释与可视化建议；principal 不接受客户端传入值，由服务端身份映射决定。
