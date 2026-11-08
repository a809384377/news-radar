# 可选信源：X 与 RSSHub

基础 RSS / 网页使用不需要这一步。先完成仓库根 README 中的安装与初始化。
以下命令均在仓库根目录执行，且只操作本仓库的运行目录：

```bash
export NEWSBOX_HOME="$PWD/.data/newsbox"
export NEWS_RADAR_AI_HOME="$PWD/.data/news-radar-ai"
```

## X / Twitter

完整目录保留了 42 个 X 信源，初始安全配置将它们禁用。
需要使用你有权访问的账号、遵守平台条款，并自行在本机安全配置 cookies。
不要把 cookies 放到 Git、聊天或 Issue 中。

原始 [twikit 接入指南](newsbox/twikit-setup.md) 中的用户级默认目录，
在此仓库应替换成 `.data/newsbox/`。登录态文件名仍为 `twikit_cookies.json`。
仅启用确实需要的源，例如：

```bash
uv run newsbox sources enable x_karpathy
uv run newsbox fetch --source x_karpathy --since 24h
```

信源 ID 以 `uv run newsbox sources list` 为准。不要尝试绕过访问限制，
也不要持续重试限流或失效登录态。X 端变化可能让适配器暂时失效。

## RSSHub（Docker 可选）

确认已经安装且启动 Docker 后，可明确执行：

```bash
uv run newsbox setup
```

此命令会启动 RSSHub / Redis 等服务，和仓库的 `scripts/setup.py` **不是同一回事**。
随后按实际需要启用清单中指向本地 RSSHub 的源。

端口、容器与停止方式见 [RSSHub 运维说明](newsbox/rsshub-setup.md)。
停止容器用 `uv run newsbox teardown`，不会删除已采集数据。

## 恢复完整上游清单

仅首次初始化新运行目录时，`scripts/setup.py --all-sources` 会保留上游默认启用状态，
意味着你需要自行准备 X 登录态和 RSSHub。它不会覆盖已经存在的信源清单。
已有运行目录请通过 `newsbox sources enable/disable` 单独管理；不要覆盖私人配置。
