# MineSentinel 监控管理报告 AI

MineSentinel 是一个面向 AstrBot 的 Minecraft 运行日志监控与管理报告插件。它只读读取服务器 `latest.log`、历史 `.log` 和 `.log.gz`，通过规则归因、异常检测、训练数据式清洗和 AstrBot 模型能力生成可直接发给管理组的五段式报告。

当前项目已经从旧的 Minecraft Adapter 转为纯监控报告 AI：不再包含 Java 端插件、WebSocket、聊天桥、远程命令、玩家绑定或跨端控制链路。仓库名 `astrbot_plugin_minecraft_adapter` 仅为了兼容原 AstrBot 插件仓库与历史安装路径。

## 核心能力

- 直接读取单服、Velocity 群组服和多后端服日志；每个 source 可配置独立 `server_id`、`server_name`、`server_type` 和投递目标。
- 启动时回扫最近窗口，实时尾读 `latest.log`，支持日志轮转、截断、重启补读和 `.log.gz` 归档读取。
- 将日志解析为 observation，写入 JSONL，并保留 OpenTelemetry Logs Data Model 风格字段，便于后续接 Loki / OTel-compatible 系统。
- 对重复 ERROR/WARN/Exception 做循环过滤，突发 backlog 不静默丢弃，避免报错风暴把 AI prompt 和存储打爆。
- 可选 Drain3 模板化与 EWMA/分位数异常检测，识别 `new_template`、`anomaly_spike`、突发 TPS/MSPT/GC/网络/插件异常。
- 先做确定性分类和事故聚合，再把压缩后的证据交给 AI；AI 负责表达和归纳，不承担第一层检测。
- 输出五段式报告、图片正文、文本兜底和完整窗口 JSONL/JSONL.GZ 附件。

## 分析链路

```text
raw log line
  -> sanitize              去 ANSI / 控制字符 / 超长行裁剪
  -> runtime hints          快速抽取时间、等级、线程、插件、聊天、Vulcan、Hikari、ops hint
  -> template/anomaly       Drain3 模板、EWMA 基线、新模板和突增标记
  -> loop filter            合并同类死循环报错
  -> rule classifier        确定性分类、严重级别、推荐动作、事故聚合
  -> LLM clean              URL/邮箱/UUID/IP/token 脱敏，质量评分，clean hash 去重
  -> prompt sampling        按重要度、异常、结构化上下文抽样，过滤低价值日常指标
  -> report sections        五段式监控管理报告
  -> delivery/export        AstrBot 会话投递、图片渲染、JSONL 附件
```

## 五段式报告

报告固定使用以下 section id，方便前端、图片渲染和后续自动化消费：

- `overall`：总体状态、窗口范围、服务器健康概览。
- `incidents`：明确异常事件，例如插件报错、网络超时、数据库连接、经济/商店问题。
- `community`：社区管理和聊天秩序，例如举报、刷屏、封禁、禁言、反作弊告警。
- `player_problems`：玩家问题/投诉识别，例如卡顿反馈、进服失败、功能不可用。
- `risk_actions`：风险、处置建议和下一步动作。

## 智能分类

内置分类包括 `daily`、`complaint`、`bug`、`network`、`plugin`、`economy`、`community`、`chat_review`、`player_feedback`、`community_ops`、`moderation`、`cross_server`、`suggestion`。

分类优先使用结构化 runtime hints 和 ops hints，再回退到关键词、上下文、日志等级、线程、插件名和事故聚合。真实样本 `tests/fixtures/mclogs_pbfhCaI.log` 来自 [mclo.gs/pbfhCaI](https://mclo.gs/pbfhCaI)，用于验证 QuickShop/经济异常、数据库异常、插件异常、网络异常会进入正确分类，同时 Hikari 生命周期日志、AstrbotAdapter/CMI 正常代理握手不会误报为管理事件。

可用配置控制分类入口：

```yaml
mine_sentinel:
  runtime_log:
    category_enabled:
      chat_review: true
      player_feedback: true
      cross_server: false
    category_whitelist: []
```

`category_whitelist` 非空时只保留白名单分类；`category_enabled` 可按分类关闭检查项。`daily` 是兜底分类，始终保留。

## Rust 加速

`mine_sentinel_rs` 是可选 PyO3 扩展，不安装也能用纯 Python 路径完整运行。安装后会加速热路径：

- JSONL codec：`normalize_record`、`record_to_json`、`json_line`、`dedupe_key`。
- runtime hints：日志等级、时间、线程、插件、聊天、Vulcan、Hikari、ops hint 批处理。
- observation priority：高日志量窗口下的优先级抽样。
- AI sampling features：prompt 入模前的清洗 key、质量评分、低价值指标过滤。

推荐从 GitHub Actions 的 `Build Rust wheels` 下载对应平台 wheel：

```bash
pip install mine_sentinel_rs-<version>-<platform>.whl
python -c "import mine_sentinel_rs; print('rust core enabled')"
```

本地开发可运行：

```bash
cargo fmt --manifest-path rust/Cargo.toml --check
cargo check --manifest-path rust/Cargo.toml
```

Windows 本地 `cargo check` 需要 MSVC `link.exe`。目标机器不需要安装 Rust；缺少 wheel 时插件自动降级，不会影响 AstrBot 加载。

## 安装

将仓库放入 AstrBot 插件目录，保持目录名为 `astrbot_plugin_minecraft_adapter`：

```bash
git clone https://github.com/EllanServer/astrbot_plugin_minecraft_adapter.git
pip install -r astrbot_plugin_minecraft_adapter/requirements.txt
```

在 AstrBot 插件管理中启用后，插件会注册 `/ms` 命令组，并在数据目录下使用 `plugin_data/mine_sentinel`。首次启动若发现旧路径 `plugin_data/astrbot_plugin_minecraft_adapter/mine_sentinel`，会自动迁移 MineSentinel 历史数据。

## 可复制 Prompt

全新安装时可以把下面这段发给 Codex、服务器运维助手或自动化执行器：

```text
请把 MineSentinel 安装到当前 AstrBot 实例中。仓库地址是 https://github.com/EllanServer/astrbot_plugin_minecraft_adapter.git，插件目录名必须保持为 astrbot_plugin_minecraft_adapter。请先确认 AstrBot 插件目录位置，再 clone 或复制仓库，执行 pip install -r requirements.txt，保留 Python 纯实现可运行；如果当前平台已有 mine_sentinel_rs wheel，再额外安装 wheel 启用 Rust 加速。然后按 README 的最小配置写入我的 Minecraft/Velocity 日志源、投递目标和报告周期，重启 AstrBot，最后用 /ms monitor status 和 /ms report now 验证插件已经读取日志并能生成五段式监控管理报告。执行前不要删除 plugin_data 中已有数据。
```

已安装旧版或历史 Minecraft Adapter 时，用下面这段做升级：

```text
请把当前 AstrBot 插件 astrbot_plugin_minecraft_adapter 升级为最新 MineSentinel。升级前先记录当前分支、未提交改动、配置文件和 plugin_data 路径，并备份 plugin_data/astrbot_plugin_minecraft_adapter/mine_sentinel 与 plugin_data/mine_sentinel。随后切到 main 分支，拉取 https://github.com/EllanServer/astrbot_plugin_minecraft_adapter.git 的最新代码，执行 pip install -r requirements.txt；如果有匹配平台的 mine_sentinel_rs wheel，请升级安装以启用 Rust 加速。保留现有 mine_sentinel 配置，移除旧 Minecraft Adapter 的远程命令、聊天桥、玩家绑定等废弃配置，只保留日志监控、报告投递和导出配置。重启 AstrBot 后检查自动数据迁移是否完成，再运行 /ms monitor status 和 /ms report now 8h，确认报告包含 overall、incidents、community、player_problems、risk_actions 五段，并且不会把正常启动/关闭/连接池生命周期日志误报为管理事件。
```

## 最小配置

```yaml
enabled: true
mine_sentinel:
  enabled: true
  retention_minutes: 480
  runtime_log:
    enabled: true
    sources:
      - server_id: survival
        server_name: 生存服
        server_type: minecraft
        root: /opt/minecraft/survival
        delivery_targets:
          - group:123456789
      - server_id: velocity
        server_name: 群组入口
        server_type: velocity
        logs_dir: /opt/minecraft/velocity/logs
    poll_interval_seconds: 5
    max_bytes_per_poll: 262144
    max_lines_per_poll: 200
    loop_filter_enabled: true
    template_parse_mode: all
  report:
    enabled: true
    interval_hours: 8
    default_window_minutes: 480
    delivery_targets:
      - group:123456789
    send_to_target_sessions: true
    send_as_image: true
    send_full_log_file: true
    export_format: jsonl
```

`sources` 支持字符串或对象。字符串可以是服务器根目录、`logs` 目录或 `latest.log` 路径；对象中 `log_file` 优先级最高，其次 `logs_dir`，最后 `root/logs/latest.log`。

投递目标建议优先使用 `/sid` 输出的完整 UMO，例如 `napcat:GroupMessage:123456`；也支持 `group:`、`qq:` 简写。source 级 `delivery_targets`/`target_sessions` 用于单服单独投递，全局 `mine_sentinel.report.delivery_targets` 用于周期总报告。

## 命令

- `/ms help`：查看 MineSentinel 命令。
- `/ms monitor status`：查看日志源、轮询、backlog、异常检测和报告状态。
- `/ms report now [服务器ID] [30m|8h]`：立即生成指定窗口报告；不传服务器 ID 时生成全局报告。

## Hourly 模式

如果只需要定期总结，不需要实时尾读，可以启用按小时总结模式。它每整点读取上一小时日志，支持 `.log.gz` 归档，生成小时摘要；累积 `hours_per_cycle` 后再整合为周期报告。

```yaml
mine_sentinel:
  runtime_log:
    enabled: false
  hourly_summary:
    enabled: true
    hours_per_cycle: 8
    poll_enabled: false
    max_records_per_hour: 5000
    max_log_lines_per_hour: 20000
  report:
    enabled: false
    delivery_targets:
      - group:123456789
```

该模式不持续轮询 `latest.log`，适合高负载服或只想要管理日报/班次报告的场景。未配置 LLM provider 时会退回规则启发式摘要。

## 性能建议

小服或默认部署：

```yaml
mine_sentinel:
  runtime_log:
    poll_interval_seconds: 5
    max_bytes_per_poll: 262144
    max_lines_per_poll: 200
    io_workers: 0
  report:
    max_records_for_ai: 160
    max_samples_per_issue: 4
```

大型多服或高日志量部署：

```yaml
mine_sentinel:
  runtime_log:
    poll_interval_seconds: 10
    max_bytes_per_poll: 524288
    max_lines_per_poll: 1000
    io_workers: 2
    anomaly_track_info: false
  report:
    max_records_for_ai: 240
    max_samples_per_issue: 3
    send_full_log_file: true
    export_format: jsonl.gz
```

关键原则：日志读取有字节/行数上限，报告窗口有采样上限，重复事故先聚合再入模。不要把原始日志整段塞给 AI。

## 开发与验证

推荐验证命令：

```bash
python -m unittest discover -s tests
python -m compileall -q services tests scripts
cargo fmt --manifest-path rust/Cargo.toml --check
cargo check --manifest-path rust/Cargo.toml
```

真实日志回归重点：

```bash
python -m unittest tests.test_mine_sentinel.MineSentinelRealLogPbfhCaITests
```

该测试会确认显式 `log_file` 只读取指定文件，pbfhCaI 样本能识别 `bug`、`plugin`、`network`、`economy`，五段式 section id 稳定，并过滤 Hikari/AstrbotAdapter/CMI 生命周期噪声。

## 迁移说明

- 旧 Adapter 的 Java 插件、WebSocket、聊天桥、远程命令、玩家绑定数据不会再被读取。
- MineSentinel observation、报告、导出附件统一放在 `plugin_data/mine_sentinel`。
- 旧 `.idx` 偏移索引若来自早期版本，建议删除后让新版本重建，以获得单调时间戳 seek 和窗口读取一致性。

## 许可证

本仓库根目录 `LICENSE` 为 GNU AGPL-3.0。图片报告默认字体缓存沿用项目原有字体策略；若使用 LXGW WenKai GB，字体项目采用 SIL Open Font License 1.1。
