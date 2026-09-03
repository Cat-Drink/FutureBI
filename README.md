# FutureBI — 企业级 ChatBI（Data Agent）基础底座

遵循 **规格驱动开发（Spec-Driven）**：不允许大模型直接生成裸 SQL，而是走
**自然语言 -> 结构化 DSL（JSON） -> 确定性 SQL 编译器** 的路线，从机制上保证
零幻觉、零注入、零随意 Join。

## 1. 技术栈

| 组件 | 选型 |
| --- | --- |
| 语言 | Python 3.11+ |
| 数据校验 | Pydantic V2（严格类型标注 + extra=forbid） |
| 本地数仓 | DuckDB（零成本开发 / 评测 / 单测） |
| 评测 | Golden Dataset + pytest |

核心约束：查询逻辑必须受限于 DSL 结构，SQL 只能由确定性编译器产出，字段必须
在语义目录（semantic.catalog）登记，连接关系只允许星型模型的受控 Join。

## 2. 工程目录结构（模块 A）

```text
FutureBI/
├── README.md                 # 本文档
├── pyproject.toml            # 项目元数据与 pytest 配置
├── requirements.txt          # 运行依赖
├── .gitignore
├── config/
│   ├── __init__.py
│   └── settings.py           # 项目根目录、DB 路径、评测锚点日期
├── semantic/                 # 语义层（解耦）
│   ├── __init__.py
│   ├── dsl_schema.py         # 受限查询 DSL（Pydantic V2）
│   └── catalog.py            # 逻辑字段 -> 物理表/列 受控映射
├── agent/                    # Agent 编排（解耦）
│   ├── __init__.py
│   ├── pipeline.py           # run_pipeline(query) -> QueryDSL 插槽
│   ├── agent.py              # LLMNL2DSL：JSON + 严格校验 + 失败重试
│   ├── heuristic.py          # 确定性启发式兜底（离线可跑）
│   ├── llm.py                # OpenAI 兼容客户端（纯标准库）
│   ├── prompts.py            # Prompt 模板
│   └── errors.py             # PipelineError（拒绝而非猜测）
├── compiler/                 # SQL 编译器（解耦）
│   ├── __init__.py
│   └── sql_compiler.py       # QueryDSL -> 确定性 DuckDB SQL
├── eval/                     # 评测引擎（解耦）
│   ├── __init__.py
│   ├── golden_dataset.json   # 10 个标准问答对
│   └── eval_runner.py        # 自动评测骨架
├── mock/                     # 模拟数据（解耦）
│   ├── __init__.py
│   ├── init_duckdb.py        # 建库 + 灌数据（固定种子，可复现）
│   └── metadata.py           # 字段中文业务注释
└── tests/                    # 单测
    ├── __init__.py
    ├── conftest.py           # 内存 DuckDB fixture
    ├── test_schema.py        # DSL 契约测试
    ├── test_compiler.py      # 编译器测试
    └── test_eval.py          # Golden 端到端评测
```

## 3. 模块说明

### 模块 B：语义 DSL（semantic/dsl_schema.py）

- `metrics`：度量列表，含 `aggregate`（sum/count/count_distinct/avg/min/max）
  与 `ratio`（如 ARPU = GMV / 去重用户数）两类指标。
- `dimensions`：切片维度（channel/category/province 等）。
- `time_filter`：结构化时间过滤，含 `granularity`（day/week/month）、
  相对/绝对时间跨度，以及同比/环比标记 `comparison`（yoy/mom，标记已定义，
  计算二期实现）。
- `filters`：通用过滤（字段、操作符 eq/in/gt/lt/between、值）。
- `order_by`：排序字段与方向。
- `limit`：截断条数，默认安全值 100。

所有模型 `extra="forbid"`，字段/操作符/聚合函数均为受限枚举。

### 模块 C：本地 Mock 数据（mock/init_duckdb.py）

自动创建 `analytics_sandbox.duckdb`，随机生成 3 张表（固定随机种子，可复现）：

1. `dim_user`：user_id, province, gender, register_time（200 行）
2. `dim_product`：product_id, category, brand, unit_price（60 行）
3. `fact_orders`：order_id, user_id, product_id, order_amount, discount_amount,
   pay_status(SUCCESS/CANCELLED), order_time（3000 行，分布在锚点日期前 90 天）

并写入 `_field_metadata` / `_table_metadata` 字段中文注释清单。

### 模块 D：Golden Dataset 与评测（eval/）

`golden_dataset.json` 含 19 个覆盖不同场景的高频问答对（单/多指标聚合、维度拆分、
相对时间"上个月"、多过滤组合、Top N、时间趋势、计数、去重、ARPU、in/between、
多指标同环比、窗口累计/移动平均、日期补零、分组 Top-N）。

`eval_runner.py` 对每个用例断言：① run_pipeline 返回的 DSL 与预期一致；
② 编译 SQL 与 golden 标准 SQL 在 DuckDB 执行后结果集一致（含 sha256 结果哈希）。

## 4. 运行步骤

```bash
# 0) 安装依赖
pip install -r requirements.txt

# 1) 初始化本地数仓（生成 analytics_sandbox.duckdb）
python -m mock.init_duckdb

# 2) 跑全量评测（连接文件库）
python -m eval.eval_runner
python -m eval.eval_runner --print-sql   # 额外打印编译 SQL

# 3) 跑单测（内存库，不依赖文件）
pytest -v
```

## 5. 二期：接入真实 NL -> DSL Agent（已完成）

`agent/` 现已实现两条可插拔路径，统一暴露 `run_pipeline(query) -> QueryDSL`：

| 路径 | 实现 | 触发条件 |
| --- | --- | --- |
| LLM Agent | `agent/agent.py` LLMNL2DSL：LLM 产出 JSON -> `QueryDSL.model_validate` 严格校验 -> 失败反馈重试（零幻觉） | 配置 `LLM_API_KEY` |
| 启发式兜底 | `agent/heuristic.py` DeterministicNL2DSL：规则化 NL -> DSL，覆盖 golden 高频问法 | 未配置 API Key（离线可跑） |

配置（两步，推荐用 `.env` 文件）：
```bash
# 1) 从模板创建本地配置文件（已加入 .gitignore 思路：密钥不入库）
copy .env.example .env

# 2) 编辑 .env，填入你的 Key 与端点
LLM_API_KEY=sk-你的密钥
LLM_BASE_URL=https://api.openai.com/v1     # 任意 OpenAI Chat Completions 兼容端点
LLM_MODEL=gpt-4o-mini
LLM_TEMPERATURE=0.0
LLM_TIMEOUT=60
LLM_MAX_RETRIES=2
```
- 项目启动时 `config/settings.py` 会自动加载根目录 `.env`（依赖 `python-dotenv`）。
- 环境变量优先级高于 `.env` 文件，便于 CI/容器注入真实密钥。
- 常用 OpenAI 兼容端点（改 `LLM_BASE_URL`/Key 即可）：OpenAI、DeepSeek
  (`https://api.deepseek.com/v1`)、Moonshot Kimi (`https://api.moonshot.cn/v1`)、
  vLLM/Ollama 本地服务等。

评测可切换 run_pipeline 来源：
```bash
python -m eval.eval_runner                    # oracle：golden 标准答案（自闭环）
python -m eval.eval_runner --pipeline agent   # 真实 Agent（无 Key 时启发式兜底）
```

## 6. 三期：同比/环比（comparison）计算（已完成）

`TimeFilter.comparison` 支持 `mom`（环比）/ `yoy`（同比），编译器生成
`cur` / `prev` 双窗口 CTE 并输出三列（以指标别名 `gmv` 为例）：

| 列 | 含义 |
| --- | --- |
| `gmv` | 当前周期值 |
| `gmv_prev` | 基准周期值（环比=前一月；同比=去年同期） |
| `gmv_mom` / `gmv_yoy` | 增长率 = (cur - prev) / NULLIF(prev, 0)，prev 为 0 时输出 NULL |

- 启发式 NL2DSL 已支持"环比/同比"问法；
- mock 数据时间跨度从 90 天扩到约 400 天（>1 年），保证同比有历史数据；
- golden 新增 Q11（环比）/ Q12（同比），双模式评测 12/12。

## 7. 四期：多事实表语义模型（已完成）

新增第二事实表 `fact_refunds`（退款表），通过 `order_id` 与主事实表
`fact_orders` **1:1 LEFT JOIN**（每订单至多一条退款）——从业务上保证无扇出
放大，跨事实表聚合结果正确。

| 逻辑字段 | 物理位置 | 说明 |
| --- | --- | --- |
| `refund_id` / `refund_amount` / `refund_time` / `refund_status` | `fact_refunds` | 退款事实表字段 |

- `semantic/catalog.py`：新增 `FACT_TABLES` / `FACT_JOIN_RULES`，第二事实表
  连接由目录声明、编译器按引用自动 LEFT JOIN；
- 支持跨事实表**比率指标**（如退款率 = `SUM(refund_amount)/SUM(order_amount)`）；
- golden 新增 Q13（各品类退款金额）、Q14（退款率），双模式评测 14/14。

## 8. 五期：权限控制（表级/列级/行级 RLS，已完成）

新增 `security/` 模块，在 DSL 生成后、编译前施加主体（principal）权限守卫：

| 权限 | 机制 | 策略示例 |
| --- | --- | --- |
| 表级 | 引用表的并集必须 ⊆ allowed_tables | restricted 不能访问 fact_refunds |
| 列级 | 引用字段不得 ∈ forbidden_columns | restricted 不能看 discount_amount/refund_amount |
| 行级 RLS | 强制注入 row_filters 过滤条件 | analyst 只能看 5 省、restricted 只能看广东 |

- `security/policy.py`：不可变 Policy 模型 + 内置 admin/analyst/restricted 三套策略；
- `security/guard.py`：`apply_policy(dsl, principal)` 纯函数，拒绝抛 `SecurityError`；
- `agent/pipeline.py`：`run_pipeline(query, principal=None)` 可选主体，None 等价 admin（向后兼容）；
- 测试：`tests/test_security.py` 9 个用例（表级拒绝/放行、列级拒绝/放行、RLS 注入与结果校验、未知主体、端到端拒绝）。

## 9. 六期：展示层（解释 + 可视化推荐，已完成）

新增 `present/` 模块，把结构化 DSL 确定性转成人类可读结果，补齐 Data Agent
"可解释、可展示"能力（纯函数、零 LLM、零外部依赖）：

- `present/labels.py`：逻辑字段/聚合/操作符/枚举值的中文标签映射；
- `present/explain.py`：`explain(dsl) -> str`，把指标/维度/过滤/时间/排序/limit
  翻译成一句中文业务话术；
- `present/viz.py`：`recommend_viz(dsl, columns, rows) -> str`，按结果形状推荐
  图表类型（number/line/bar/pie/table），`viz_config` 输出前端可用的 x/y 配置。

用法示例：
```python
from agent.pipeline import run_pipeline
from present.explain import explain
from present.viz import recommend_viz

dsl = run_pipeline("各品类成功订单的GMV分布？")
print(explain(dsl))                 # 查询指标：gmv（求和订单金额），按 类目 分组，筛选条件：支付状态 等于 成功，最多返回 100 条。
```

验证 LLM 路径是否生效：
```bash
# 返回 LLMNL2DSL 说明已启用 LLM；返回 DeterministicNL2DSL 说明在启发式兜底
python -c "from agent.pipeline import _default_agent; print(type(_default_agent()).__name__)"
# 把一句中文问题走完 NL->DSL->SQL 全链路
python -c "from agent.pipeline import run_pipeline; from compiler.sql_compiler import compile_sql; print(compile_sql(run_pipeline('2024年6月成功订单的总销售额是多少？')))"
```

## 10. Web 可视化 UI（可交互界面，已完成）

新增 `web/` 模块，把「自然语言 → DSL → 确定性 SQL → 结果 → 解释 + 图表」
串成一个零依赖、可交互的 Web 界面（标准库 http.server + 原生 SVG，无前端框架）：

- `web/service.py`：`run_query(query, principal, conn)` 执行完整链路，返回
  `{dsl, sql, columns, rows, explanation, viz}`；任何阶段失败都以 `error` 字段返回；
- `web/server.py`：HTTP 服务（`GET /`、`GET /api/health`、`POST /api/query`）；
- `web/static/`：单页前端，支持 number/bar/pie/line 四类图表与数据表渲染，
  内置权限主体下拉（admin/analyst/restricted）即时验证行级 RLS。

启动与访问：
```bash
python -m web.server 8000
# 浏览器打开 http://127.0.0.1:8000
```

API 示例：
```bash
curl -X POST http://127.0.0.1:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"query":"各品类成功订单的GMV分布？","principal":"analyst"}'
```

离线自检（无需真实 Key）：`tools/mock_llm_server.py` 是本地 OpenAI 兼容
Chat Completions 模拟服务，可验证客户端协议握手与围栏剥离：
```bash
python tools/mock_llm_server.py 8765          # 终端 A：启动模拟端点
# 终端 B（指向模拟端点）：
set LLM_API_KEY=sk-mock & set LLM_BASE_URL=http://127.0.0.1:8765/v1 & set LLM_MODEL=mock
python -c "from agent.pipeline import run_pipeline; print(run_pipeline('2024年6月GMV多少').model_dump_json())"
```

零幻觉保障：LLM 输出先经 Pydantic 严格校验（`extra=forbid`、受限枚举），非法输出
自动重试并反馈错误；重试耗尽即抛 `PipelineError` 拒绝回答，绝不猜测 SQL。

## 11. 八期：进阶语义能力（多指标同环比 / 窗口函数 / 日期补零 / 分组 Top-N，已完成）

在既有聚合/比率/同比环比基础上，新增四类进阶语义，均由确定性编译器生成 SQL：

| 能力 | DSL 结构 | 编译形态 | 示例问法 |
| --- | --- | --- | --- |
| 多指标同环比 | metrics 含多个指标 + time_filter.comparison | cur/prev 双窗口 CTE，逐指标输出 prev 与增长率 | "2024年6月成功订单的GMV和订单数的环比是多少？" |
| 窗口-累计 | metrics=[{kind:window, func:cumsum}] + 时间维度 | SUM(聚合) OVER (ORDER BY 时间) | "每日成功订单GMV的累计值？" |
| 窗口-移动平均 | func:moving_avg + window_size=N | AVG(聚合) OVER (ORDER BY 时间 ROWS BETWEEN N-1 PRECEDING AND CURRENT ROW) | "每日GMV的7日移动平均？" |
| 日期连续补零 | fill_gaps=true + 时间维度 + 明确时间窗口 | generate_series 连续日期 spine LEFT JOIN 聚合结果，指标 COALESCE 为 0 | "每日GMV趋势（补零）？" |
| 分组 Top-N | top_n={n, partition_by, order_by} | ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...) 再过滤序号 <= n | "每省成功订单GMV Top 3 品类？" |

语义契约（`semantic/dsl_schema.py`）：
- `WindowMetric`（kind=window）：base 为 AggregateMetric，func 为 cumsum/moving_avg，
  moving_avg 必须提供 window_size；
- `TopN`：n >= 1、partition_by 非空维度列表、order_by 非空排序列表；
- `QueryDSL.fill_gaps`：布尔标记，缺省 false。

编译器（`compiler/sql_compiler.py`）新增 `_compile_with_top_n`、
`_compile_with_fill_gaps` 与普通路径内的窗口指标展开，并对互斥组合
（comparison / top_n / fill_gaps / window 同现）显式抛 CompileError 拒绝。
golden 新增 Q15–Q19，双模式评测 19/19。

## 12. 九期：意图路由 + 语义澄清反问（P0，已完成）

在生成 DSL 之前新增意图路由闸门 `agent/router.py::route_query(query)`，
对输入做**显式三分类**与**语义澄清反问**，禁止静默回退默认值：

| 意图 | 触发条件 | 动作 |
| --- | --- | --- |
| `text2sql` | 数据分析查询 | 进入 NL -> DSL -> SQL 链路 |
| `rag` | 询问指标口径/定义/怎么算 | 检索 `agent/glossary.py` 口径文档 |
| `chitchat` | 闲聊/寒暄/越界话题 | 礼貌拒绝，不进入链路 |

**语义澄清反问**（对 text2sql 命中时返回 `clarify`，绝不静默回退）：

- **缺失时间窗口**：例如"成功订单的GMV是多少？"缺少时间范围，
  返回 `missing_time_window` 反问，避免默认全量历史；
- **未定义业务指标**：例如"高活用户/高活跃用户"未定义口径，
  返回 `undefined_metric` 反问；启发式（`agent/heuristic.py`）同步加守卫，
  宁可拒绝也不把"高活跃用户"近似映射为已定义的"活跃用户"。

**口径文档 RAG 检索**（`agent/rag.py`，零依赖、确定性）：
从 `agent/glossary.py` 的口径词典按"别名命中 + 字符 bigram 重叠"打分检索，
返回指标的业务定义与计算公式，例如"GMV 的口径是什么？"命中 gmv 文档。

**接入**：`web/service.py::run_query` 现在先经 `route_query` 路由，
响应新增 `intent` / `action` / `message` / `clarifications` / `documents`
字段，Web UI 相应渲染澄清问题与口径文档。

用法示例（代码块）：
```python
from agent.router import route_query
r = route_query("高活用户的GMV是多少？")
print(r.intent.value, r.action.value)      # text2sql clarify
print(r.clarifications[0].kind)            # undefined_metric
print(r.clarifications[0].term)            # 高活用户
```

测试：`tests/test_router.py`（21 个用例），全量 `pytest` 保持绿色。
