"""展示层：DSL -> 自然语言解释 + 可视化推荐。"""

from present.explain import explain
from present.viz import recommend_viz, viz_config

__all__ = ["explain", "recommend_viz", "viz_config"]
