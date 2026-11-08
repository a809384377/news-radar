# Collector reference

Use the repository [quick start](../../README.md) to install both components.
The full built-in catalog contains 76 source definitions (30 RSS, 4 web, 42 X);
2 definitions are disabled upstream. The default setup enables only direct
RSS/web entries, excluding X and local RSSHub dependencies. Availability is
not guaranteed; sources may rate-limit, move or require regional connectivity.

- [CLI and operations skill](../../skills/newsbox/SKILL.md)
- [Python SDK](sdk-usage.md)
- [X/Twitter optional setup](twikit-setup.md)
- [RSSHub optional setup](rsshub-setup.md)
- [Full source catalog](sources.seed.yaml)

The historical install guide describes the independently distributed newsbox
package. For this combined repository, use `uv sync --locked` at the root.
