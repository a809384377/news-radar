"""helpers — 工具层原子能力（sprint lean-data-and-flow 精简后）。

- pull          — newsbox SDK → ai.db 幂等 INSERT 'pending'
- filter_score  — 批量 DeepSeek per-article（'pending' → 'scored'/'filter_rejected'/'failed'）
- queries       — 报告候选池只读查询（report_candidates）
- preflight     — 环境自检
- config_writer — config.yaml 单字段写入
"""
