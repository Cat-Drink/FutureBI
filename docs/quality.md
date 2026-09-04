# 质量保障

## 本地检查

```bash
black --check .
ruff check .
python -m pytest -q
```

## Golden 评测

`eval/golden_dataset.json` 包含 19 个覆盖聚合、维度、时间、过滤、Top-N、窗口、补零等场景的问答用例。评测同时检查 DSL 结构与 SQL 执行结果，并使用结果哈希保证可复现：

```bash
python -m eval.eval_runner
python -m eval.eval_runner --pipeline agent
```

## CI

`.github/workflows/ci.yml` 在 push 与 pull request 中运行：

- black 格式检查与 ruff 静态检查；
- pytest 全量测试；
- oracle 与 agent 双模式 Golden 评测。

每日夜跑还会执行完整测试、Golden 双模式及 RLS 对抗矩阵。

## 可复现锚点

- `AS_OF_DATE = 2024-06-30`；
- 随机种子 42；
- Mock DuckDB 可重复初始化。
