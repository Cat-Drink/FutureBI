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

`golden_dataset.json` 含 10 个覆盖不同场景的高频问答对（单指标聚合、维度拆分、
相对时间"上个月"、多过滤组合、Top N、时间趋势、计数、去重、ARPU、in/between）。

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

验证 LLM 路径是否生效：
```bash
# 返回 LLMNL2DSL 说明已启用 LLM；返回 DeterministicNL2DSL 说明在启发式兜底
python -c "from agent.pipeline import _default_agent; print(type(_default_agent()).__name__)"
# 把一句中文问题走完 NL->DSL->SQL 全链路
python -c "from agent.pipeline import run_pipeline; from compiler.sql_compiler import compile_sql; print(compile_sql(run_pipeline('2024年6月成功订单的总销售额是多少？')))"
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
