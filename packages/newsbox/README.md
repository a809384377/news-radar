# newsbox — collection layer

This package collects RSS/Atom, web pages and optional X/Twitter sources into SQLite.
It exposes the `newsbox` CLI and `newsbox.sdk.read_raw()` Python SDK.

From the repository root run `uv sync --locked`, then follow the
[quick start](../../README.md). Do not install another PyPI copy of newsbox:
the workspace uses the source included here.

- [Collector guide](../../docs/newsbox/README.md)
- [Collector skill](../../skills/newsbox/SKILL.md)
- [SDK guide](../../docs/newsbox/sdk-usage.md)
- [Full source catalog](src/newsbox/data/sources.seed.yaml)

Python 3.11+. MIT. X requires your own authorized cookies; RSSHub is optional.
