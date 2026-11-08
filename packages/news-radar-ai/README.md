# news-radar-ai — AI processing layer

Consumes newsbox via its Python SDK, filters noise, produces industry-radar and
practical-experience scores, Chinese summaries and report candidates.

From the repository root run `uv sync --locked`, then follow the
[quick start](../../README.md). The included newsbox package is resolved locally.

- CLI: `uv run news-radar-ai --help`
- [Workflow skill and report template](../../skills/news-radar-ai/SKILL.md)
- [Standalone pipeline](../../scripts/run.py)

Real AI scoring requires the user's own DeepSeek API key and incurs API charges.
The standalone report is deterministic; agent-driven clustering and editorial
insights require an AI assistant following the skill. Python 3.11+. MIT.
