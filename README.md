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
│   └── pipeline.py           # run_pipeline(query) -> QueryDSL 插槽
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

## 5. 后续接入 Agent

评测器预留 `run_pipeline(query) -> QueryDSL` 插槽。接入 LLM 时：
1. LLM 产出 JSON -> `QueryDSL.model_validate(...)` 严格校验，非法即拒绝；
2. 将真实 Agent 实现替换 `agent/pipeline.py` 中的 `run_pipeline`；
3. 复跑 `python -m eval.eval_runner` 即可回归验证新 Agent 的准确率。
