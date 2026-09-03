# AGENTS.md

本文件面向 AI 编码代理与协作者，说明项目的运行环境、常用命令与约定。

## 项目简介

FutureBI 是规格驱动（Spec-Driven）的企业级 ChatBI / Data Agent：
自然语言 -> 结构化 DSL(JSON) -> 确定性 SQL 编译器 -> DuckDB 执行。
LLM 只产出受控 JSON，绝不直接生成裸 SQL，从结构上杜绝幻觉与注入。

## 运行环境（conda 虚拟环境）

- 环境管理器：**Miniconda / Anaconda**（conda 26.5.3）
- 专用虚拟环境名：**`futurebi`**
- Python：**3.12.x**（`requires-python = ">=3.11"`，实际锁定 3.12）
- 包管理：conda 建环境 + pip 装依赖（`requirements-dev.txt`）

### 一键创建并激活环境

```bash
conda create -n futurebi python=3.12 -y
conda activate futurebi
pip install -r requirements-dev.txt
```

> 注：本机环境已建好，位置 `C:\Users\<user>\.conda\envs\futurebi`；
> 用 `conda activate futurebi` 即可进入。

## 常用命令

```bash
# 运行测试
python -m pytest -q

# 代码格式检查（black）
black --check .

# 代码格式自动修复
black .

# Lint 检查（ruff）
ruff check .

# Lint 自动修复
ruff check --fix .

# 重建本地数仓（幂等，用于生成 DuckDB 文件）
python -m mock.init_duckdb

# 评测（oracle / agent 双模式）
python -m eval.eval_runner
python -m eval.eval_runner --pipeline agent

# 启动 Web 可视化 UI（默认 8000）
python -m web.server 8000
```

## 目录结构

```
semantic/   语义目录 + DSL 契约（Single Source of Truth）
compiler/   DSL -> SQL 确定性编译器
agent/      NL -> DSL Agent（LLM + 启发式兜底）
eval/       Golden 评测骨架与用例
mock/       确定性 mock 数仓（DuckDB）
present/    展示层（解释 + 可视化推荐）
security/   权限控制（表级/列级/行级 RLS）
config/     全局配置
tests/      单元测试
tools/      本地工具（mock LLM 服务端等）
web/        Web 可视化 UI（service + server + static 前端）
```

## 关键约定

- 所有新增逻辑字段必须登记在 `semantic/catalog.py` 的 `COLUMNS` 白名单，否则编译器拒绝；
- DSL 模型一律 `extra="forbid"`，Agent 只能产出契约内字段；
- 评测锚点 `AS_OF_DATE = 2024-06-30`、随机种子 42，保证确定性可复现；
- 提交前确保 `black --check .`、`ruff check .`、`python -m pytest -q` 全绿。

