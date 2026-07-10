"""Evidence-grounded incident facts and operator check plans."""

from __future__ import annotations

import re
import time
from typing import Any


_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}
_CONFIG_FILE_RE = re.compile(
    r"(?:plugins[/\\])?(?P<path>[A-Za-z0-9_. -]+[/\\][A-Za-z0-9_. -]+\.(?:ya?ml|json|conf|toml))",
    re.IGNORECASE,
)
_PLUGIN_EVIDENCE_PATTERNS = (
    re.compile(r"\]:\s*\[(?P<name>[A-Za-z0-9_-]{2,64})\]"),
    re.compile(r"\bplugins[/\\](?P<name>[^/\\\s]{2,64})[/\\]", re.IGNORECASE),
    re.compile(
        r"\b(?P<name>[A-Za-z][A-Za-z0-9_-]{2,48})-(?:HikariPool|Hikari|Pool)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"Craft Scheduler Thread[^\]]*? - (?P<name>[A-Za-z0-9_-]{2,48})/",
        re.IGNORECASE,
    ),
)
_EXTERNAL_SERVICE_RE = re.compile(
    r"(?:https?://|Connect to\s+)(?P<host>[A-Za-z0-9.-]+)(?::(?P<port>\d{2,5}))?",
    re.IGNORECASE,
)
_IGNORED_COMPONENTS = {
    "server",
    "minecraft",
    "paper",
    "spigot",
    "purpur",
    "velocity",
    "hikari",
    "hikaripool",
    "poolbase",
    "warn",
    "warning",
    "error",
    "info",
}


def build_incident_facts(
    issues: list[dict[str, Any]],
    *,
    fallback_time_range: str = "",
) -> dict[str, Any]:
    """Collect the five facts an operator needs: when, where, who, what, evidence."""

    first_seen = min(
        (_positive_int(issue.get("first_seen_ts")) for issue in issues),
        default=0,
    )
    last_seen = max(
        (_positive_int(issue.get("last_seen_ts")) for issue in issues),
        default=0,
    )
    servers = _collect(issues, "affected_servers")
    backends = _collect(issues, "affected_backends")
    locations = _collect(issues, "affected_locations")
    worlds = _collect(issues, "affected_worlds")
    positions = _collect(issues, "affected_positions")
    people = _collect(issues, "players")
    plugins = _collect(issues, "affected_plugins")
    log_files = _collect(issues, "affected_log_files")
    categories = _collect(issues, "ops_categories")
    subtypes = _collect(issues, "ops_subtypes")
    impacts = _collect(issues, "ops_impacts")
    samples = _collect(issues, "evidence_samples", limit=12)
    if not plugins:
        plugins = _plugins_from_evidence(samples)
    config_files = _configuration_files(samples)
    external_services = _external_services(samples)
    evidence_count = sum(_positive_int(issue.get("evidence_count")) for issue in issues)
    exact_time = _exact_time_range(first_seen, last_seen) or fallback_time_range or "时间未记录"
    duration = _duration_text(first_seen, last_seen)

    position_worlds = {
        position.split(" (", 1)[0].strip().lower()
        for position in positions
        if " (" in position
    }
    standalone_worlds = [
        world for world in worlds if world.lower() not in position_worlds
    ]
    where_parts = _unique([*locations, *backends, *standalone_worlds, *positions])
    if not where_parts:
        where_parts = list(servers)
    where = " / ".join(where_parts[:6]) or "未记录具体服务器/世界位置"
    people_text = (
        "、".join(people[:12])
        if people
        else "未关联到具体玩家（服务端/插件级事件）"
    )
    component_values = plugins or subtypes or categories
    components = _display_values(
        component_values,
        limit=5,
        fallback="未从当前证据识别到具体插件/组件",
    )
    return {
        "first_seen_ts": first_seen,
        "last_seen_ts": last_seen,
        "time": exact_time,
        "duration": duration,
        "servers": servers,
        "backends": backends,
        "locations": locations,
        "worlds": worlds,
        "positions": positions,
        "where": where,
        "people": people,
        "people_text": people_text,
        "plugins": plugins,
        "components": components,
        "log_files": log_files,
        "configuration_files": config_files,
        "external_services": external_services,
        "categories": categories,
        "subtypes": subtypes,
        "impacts": impacts,
        "evidence_count": evidence_count,
        "evidence_samples": samples,
    }


def build_check_plan(
    issues: list[dict[str, Any]],
    facts: dict[str, Any],
    family: str,
) -> list[dict[str, str]]:
    """Build concrete checks with pass criteria and escalation paths."""

    categories = set(facts.get("categories") or [])
    subtypes = set(facts.get("subtypes") or [])
    tags = {str(issue.get("tag") or "").lower() for issue in issues}
    issue_categories = {str(issue.get("category") or "").lower() for issue in issues}
    components = str(facts.get("components") or "相关组件")
    where = str(facts.get("where") or "当前服务器")
    when = str(facts.get("time") or "证据时间段")
    log_files = "、".join(facts.get("log_files") or []) or "对应 latest.log/历史日志"
    people = str(facts.get("people_text") or "未关联到具体玩家")
    evidence_count = int(facts.get("evidence_count") or 0)
    plan: list[dict[str, str]] = [
        _step(
            "证据定位",
            f"在 {where} 的 {log_files} 精确筛选 {when}，保存首条异常、完整堆栈和末次复现；"
            f"按 {components} 分组核对 {evidence_count} 条聚合证据。",
            "每条异常都能对应到时间、服务器/后端和插件或组件，且首条根因没有被重复堆栈淹没。",
            "若无法归因，扩大每个命中点前后各 40 行，并从完整日志附件按时间回查原始记录。",
        )
    ]

    database = bool(
        "数据库与存储" in categories
        or subtypes
        & {
            "数据库连接异常",
            "数据库超时",
            "玩家/世界数据保存失败",
        }
    )
    configuration = bool(
        subtypes
        & {
            "配置解析异常",
            "技能/内容定义错误",
            "外部 API 凭据缺失",
            "依赖缺失/功能降级",
            "插件不安全模式",
        }
    )
    economy = "经济与资产" in categories or "economy" in issue_categories
    network = bool(
        "网络与代理" in categories
        or "network" in issue_categories
        or "cross_server" in issue_categories
        or "server_log_network" in tags
    )
    auth = bool(
        "认证与接入安全" in categories
        or "离线模式/认证绕过风险" in subtypes
        or "moderation" in issue_categories
    )
    performance = "性能与资源" in categories or "complaint" in issue_categories
    position = bool(
        "传送与位置" in categories
        or "世界与区块" in categories
        or subtypes & {"传送/位置异常", "区块/世界异常"}
    )

    if database:
        plan.extend(_database_steps(components, where, people, economy))
    elif economy:
        plan.extend(_economy_steps(components, where, people, facts))
    if configuration:
        plan.extend(_configuration_steps(components, facts))
    if network:
        plan.extend(_network_steps(components, where))
    if auth:
        plan.extend(_auth_steps(where))
    if performance:
        plan.extend(_performance_steps(where, when))
    if position:
        plan.extend(_position_steps(where, people))
    if family in {"community", "chat_review", "moderation"} and not auth:
        plan.extend(_moderation_steps(people, when))
    if family == "player_feedback" and not position:
        plan.extend(_feedback_steps(people, where, when))
    if not any(
        (database, economy, configuration, network, auth, performance, position)
    ) and family == "operations":
        plan.extend(_plugin_steps(components, where))

    verification = build_verification(issues, facts, family)
    plan.append(
        _step(
            "恢复验证",
            verification,
            "完成标准全部满足，并记录处置人、变更内容、验证时间和证据链接。",
            "任一标准不满足就继续标记为“处理中”，不能只因重启后暂时没报错就关闭。",
        )
    )
    return _dedupe_steps(plan)[:6]


def build_reader_action(
    issues: list[dict[str, Any]],
    facts: dict[str, Any],
    family: str,
) -> str:
    """Summarize the next action without requiring operations knowledge."""

    categories = set(facts.get("categories") or [])
    subtypes = set(facts.get("subtypes") or [])
    issue_categories = {str(issue.get("category") or "").lower() for issue in issues}
    components = str(facts.get("components") or "相关插件或服务")
    where = str(facts.get("where") or "相关服务器")
    people = "、".join(facts.get("people") or []) or "相关玩家"
    database = bool(
        "数据库与存储" in categories
        or subtypes & {"数据库连接异常", "数据库超时", "玩家/世界数据保存失败"}
    )
    economy = "经济与资产" in categories or "economy" in issue_categories

    if database and economy:
        return (
            f"请服务器维护人员检查数据库能否正常连接，以及 {components} 的数据库设置；"
            "恢复后用测试账号完成一次商店交易，确认扣款、发货和余额都正确。"
        )
    if database:
        return (
            f"请服务器维护人员检查数据库能否正常连接，以及 {components} 的连接设置；"
            "恢复后测试一次受影响的功能。"
        )
    if economy:
        return (
            f"请服务器维护人员先确认 {components} 的购买、扣款和发货是否正常；"
            "如果只是翻译或更新服务暂时不可用，可稍后重试，不要误判成交易故障。"
        )
    if subtypes & {
        "配置解析异常",
        "技能/内容定义错误",
        "外部 API 凭据缺失",
        "依赖缺失/功能降级",
        "插件不安全模式",
    }:
        return (
            f"请服务器维护人员先备份，再检查 {components} 的设置文件；"
            "修正报错项后测试受影响功能，不确定时先恢复备份。"
        )
    if "离线模式/认证绕过风险" in subtypes or "认证与接入安全" in categories:
        return (
            "请服务器维护人员确认玩家只能通过代理服进入后端服，普通网络不能直接连接后端；"
            "确认完成前不要对公网开放后端端口。"
        )
    if "网络与代理" in categories or issue_categories & {"network", "cross_server"}:
        return (
            f"请服务器维护人员检查代理服到 {where} 的连接，再用测试账号重复完成进服或切服测试。"
        )
    if "性能与资源" in categories or "complaint" in issue_categories:
        return (
            f"请服务器维护人员检查 {where} 在问题发生时是否出现处理器、内存或磁盘繁忙，"
            "先处理最明显的瓶颈，再请玩家复测。"
        )
    if "传送与位置" in categories or "世界与区块" in categories:
        return (
            f"请管理员联系 {people}，在 {where} 用测试账号重走一次相同流程；"
            "确认传送位置、背包和玩家状态是否正确。"
        )
    if family in {"community", "chat_review", "moderation"}:
        return f"请社区管理员查看 {people} 的相关聊天和前后内容，确认事实后再决定是否处理。"
    if family == "player_feedback":
        return (
            f"请值班管理员联系 {people} 确认操作步骤，并用测试账号重现问题；"
            "能够重现时交给对应插件或服务器维护人员。"
        )
    return (
        f"请服务器维护人员确认 {components} 的问题是否仍在发生，"
        "修复后测试一次受影响功能，并继续观察是否再次出现。"
    )


def build_reader_verification(
    issues: list[dict[str, Any]],
    facts: dict[str, Any],
    family: str,
) -> str:
    """Explain completion criteria in language suitable for a manager."""

    categories = set(facts.get("categories") or [])
    subtypes = set(facts.get("subtypes") or [])
    people = "、".join(facts.get("people") or [])
    if "经济与资产" in categories:
        return "测试交易成功，扣款、发货和余额一致，并且 30 分钟内没有再次出现同类问题。"
    if "数据库与存储" in categories or subtypes & {"数据库连接异常", "数据库超时"}:
        return "数据库已经恢复，受影响功能测试成功，并且 30 分钟内没有再次出现同类问题。"
    if subtypes & {"配置解析异常", "技能/内容定义错误"}:
        return "相关设置可以正常加载，受影响的命令或功能测试成功，启动日志不再报同类错误。"
    if "网络与代理" in categories:
        return "测试账号连续 5 次进服或切服成功，并且 30 分钟内没有再次断开。"
    if "离线模式/认证绕过风险" in subtypes:
        return "玩家只能通过代理服进入，普通网络无法直接连接后端服，测试账号登录正常。"
    if "性能与资源" in categories:
        return "服务器运行指标恢复到平时水平，连续观察 30 分钟正常，并由玩家确认不再卡顿。"
    if "传送与位置" in categories or "世界与区块" in categories:
        return "测试账号能到达正确地点，背包和状态正确，相关玩家的数据没有再次异常。"
    if family in {"community", "chat_review", "moderation"}:
        suffix = f"（{people}）" if people else ""
        return f"管理员已核对完整聊天内容{suffix}并记录处理依据；证据不足时不执行处罚。"
    if family == "player_feedback":
        return "已经回访相关玩家；问题已解决，或者已交给明确的负责人并约定处理时间。"
    return "受影响功能测试成功，并且连续观察 30 分钟没有再次出现同类问题。"


def build_verification(
    issues: list[dict[str, Any]],
    facts: dict[str, Any],
    family: str,
) -> str:
    categories = set(facts.get("categories") or [])
    subtypes = set(facts.get("subtypes") or [])
    plugins = "、".join(facts.get("plugins") or []) or "相关插件"
    people = "、".join(facts.get("people") or [])
    if "数据库与存储" in categories or subtypes & {
        "数据库连接异常",
        "数据库超时",
    }:
        return (
            f"从 Minecraft 主机执行数据库只读连通测试成功，{plugins} 的连接池不再出现 timeout/closed connection；"
            "连续 30 分钟无同类 ERROR/WARN，并完成一次受影响功能的读写冒烟测试。"
        )
    if "经济与资产" in categories:
        services = "、".join(facts.get("external_services") or [])
        dependency = f"外部依赖 {services} 恢复，" if services else "相关依赖恢复，"
        return (
            f"{dependency}{plugins} 完成一笔测试交易，扣款、发货、余额和数据库流水一致；"
            "连续 30 分钟无同类错误，且已确认可选资源服务失败不会影响交易主链路。"
        )
    if "配置解析异常" in subtypes or "技能/内容定义错误" in subtypes:
        return (
            f"备份并修正配置后，在维护窗口加载 {plugins}；启动日志无解析/定义错误，"
            "对应命令、技能或内容完成一次冒烟测试。"
        )
    if "网络与代理" in categories:
        return "代理到目标后端连续 5 次 TCP 连接成功，玩家完成进服/切服测试，连续 30 分钟无 timeout/reset。"
    if "离线模式/认证绕过风险" in subtypes:
        return "从非代理网络无法直连后端端口，代理转发模式与 forwarding secret 一致，并完成一次测试账号登录核验。"
    if "性能与资源" in categories:
        return "TPS/MSPT、GC 和资源指标回到该服务器正常基线，连续 30 分钟无同类告警，并由玩家侧复测确认。"
    if family in {"community", "chat_review", "moderation"}:
        suffix = f"，涉及 {people}" if people else ""
        return f"管理员逐条核对原文、频率和上下文{suffix}，记录处理依据；证据不足时不得执行不可逆处罚。"
    if family == "player_feedback":
        suffix = f"（{people}）" if people else ""
        return f"完成可重复复现并回访相关玩家{suffix}；问题已解决，或已经形成有负责人和截止时间的后续工单。"
    return "连续 30 分钟无同类 ERROR/WARN，受影响功能完成一次冒烟测试，并由值班人员记录关闭依据。"


def format_check_step(item: dict[str, Any]) -> str:
    return (
        f"[{item.get('phase') or '检查'}] {item.get('check') or ''} "
        f"通过标准：{item.get('expected') or '结果符合预期'} "
        f"未通过：{item.get('on_failure') or '保持事件开启并升级处理'}"
    ).strip()


def infer_family(issue: dict[str, Any]) -> str:
    category = str(issue.get("category") or "").lower()
    if category in {"community", "chat_review", "moderation", "player_feedback"}:
        return category
    return "operations"


def _database_steps(
    components: str,
    where: str,
    people: str,
    economy: bool,
) -> list[dict[str, str]]:
    steps = [
        _step(
            "依赖检查",
            f"从 {where} 所在 Minecraft 主机测试数据库 host:port，并用只读账号执行 SELECT 1；"
            "同时查看 Threads_connected、max_connections、慢查询和磁盘 I/O。",
            "TCP 与 SELECT 1 无超时，连接数未触顶，数据库日志没有拒绝连接、锁等待或存储错误。",
            "先恢复数据库/网络/磁盘，再处理插件；不要用反复重启服务器掩盖连接耗尽或慢查询。",
        ),
        _step(
            "连接池修复",
            f"核对 {components} 的 JDBC/Hikari 配置：地址、账号权限、maximumPoolSize、connectionTimeout、"
            "maxLifetime 与数据库 wait_timeout；确认失效连接能被淘汰。",
            "连接池可重建连接，活动连接低于上限，日志不再出现 Failed to validate/connection closed。",
            "按失败插件逐个修正连接池或升级驱动；仍失败时把数据库日志、连接池配置和时间点交给数据库负责人。",
        ),
    ]
    if economy:
        steps.append(
            _step(
                "资产核对",
                f"按证据时间和人物（{people}）核对 Vault/商店流水、余额变更、物品发放与数据库提交记录；"
                "在原因确认前暂停批量补偿。",
                "每笔扣款、发货和余额变更一一对应，无重复提交、漏发或未提交事务。",
                "发现不一致时先导出差异清单并冻结相关补偿，依据可审计流水逐笔修复，禁止凭聊天直接改余额。",
            )
        )
    return steps


def _configuration_steps(components: str, facts: dict[str, Any]) -> list[dict[str, str]]:
    files = "、".join(facts.get("configuration_files") or []) or "证据中对应的 YAML/JSON/配置文件"
    return [
        _step(
            "配置修复",
            f"先备份 {files}，使用对应 YAML/JSON 解析器定位报错行；再对照 {components} 当前版本文档检查字段名、"
            "缩进、枚举值、依赖和 API 凭据。",
            "配置可独立解析，必需依赖/凭据存在，变更 diff 只包含预期字段。",
            "无法确认字段语义时恢复备份并停止热重载，把最小配置片段、插件版本和报错行交给插件负责人。",
        )
    ]


def _economy_steps(
    components: str,
    where: str,
    people: str,
    facts: dict[str, Any],
) -> list[dict[str, str]]:
    services = "、".join(facts.get("external_services") or [])
    dependency = services or "证据中的外部服务/数据库依赖"
    return [
        _step(
            "功能边界",
            f"先确认 {components} 在 {where} 失败的是交易主链路还是可选资源服务（{dependency}）；"
            "分别测试商品查询、扣款、发货和可选资源更新。",
            "明确只有哪个子功能失败；若交易成功且仅可选资源更新失败，应降级处置而不是冻结全部商店。",
            "交易主链路失败时暂停相关商品/补偿并保留流水；仅可选服务失败时关闭自动重试或使用缓存，按插件文档修复依赖。",
        ),
        _step(
            "依赖检查",
            f"从 {where} 所在主机检查 {dependency} 的 DNS、TCP/TLS 和 HTTP 响应，并对照 {components} 的超时、"
            "代理和重试配置。",
            "DNS/TLS/HTTP 连续成功，响应时间低于插件超时阈值，日志不再出现 ConnectTimeoutException。",
            "按 DNS、出口防火墙/代理、远端服务状态、插件超时配置逐层升级；禁止仅靠增加重试造成线程堆积。",
        ),
        _step(
            "资产核对",
            f"按证据时间和人物（{people}）核对 Vault/商店流水、余额变更、物品发放与数据库提交记录；"
            "在确认差异前暂停批量补偿。",
            "每笔扣款、发货和余额变更一一对应，无重复提交、漏发或未提交事务。",
            "发现不一致时导出差异清单并冻结相关补偿，依据可审计流水逐笔修复，禁止凭聊天直接改余额。",
        ),
    ]


def _network_steps(components: str, where: str) -> list[dict[str, str]]:
    return [
        _step(
            "链路检查",
            f"从代理主机到 {where} 的实际后端地址/端口做连续 TCP 测试，并核对 {components}、DNS、防火墙、"
            "Velocity/Bungee 后端状态和转发配置。",
            "目标端口连续可达、DNS 解析稳定，代理和后端在同一时间没有 timeout/reset/refused。",
            "按 DNS、路由/防火墙、后端进程、代理配置四层逐层升级；保留每层测试时间和结果。",
        )
    ]


def _auth_steps(where: str) -> list[dict[str, str]]:
    return [
        _step(
            "接入边界",
            f"核对 {where} 的 online-mode、代理 player-info-forwarding-mode、forwarding secret 与后端防火墙；"
            "从非代理网络尝试连接后端端口。",
            "后端只能由受控代理访问，转发模式/secret 一致，测试账号 UUID 与权限组正确。",
            "立即收紧后端端口来源；无法保证代理隔离时启用正版验证或暂停对公网开放该后端。",
        )
    ]


def _performance_steps(where: str, when: str) -> list[dict[str, str]]:
    return [
        _step(
            "性能归因",
            f"对照 {where} 在 {when} 的 spark/timings、TPS/MSPT、GC、堆内存、实体/区块/红石和插件任务耗时。",
            "异常时间点能对应到具体线程、插件任务或资源瓶颈，而不是只看到平均 TPS。",
            "无法定位时抓取覆盖复现窗口的 profiler，并按主线程、GC、磁盘和网络等待分别升级。",
        )
    ]


def _position_steps(where: str, people: str) -> list[dict[str, str]]:
    return [
        _step(
            "场景复现",
            f"在 {where} 使用测试账号复现相同传送/切服/换世界流程，并核对人物（{people}）的位置保存、"
            "目标区块加载、代理转发和相关插件日志。",
            "测试账号到达正确世界/坐标，背包与状态一致，原玩家记录没有保存失败或跨服覆盖。",
            "立即停止重复传送测试，备份玩家数据；按代理、传送插件、世界加载、玩家数据同步顺序定位。",
        )
    ]


def _moderation_steps(people: str, when: str) -> list[dict[str, str]]:
    return [
        _step(
            "人工复核",
            f"只查看 {when} 内与 {people} 对应的聊天原文、频道、发送频率和前后上下文；关联反作弊/处罚日志。",
            "每个管理动作都有对应原文、规则条款和复核人，聊天举报与事实证据明确区分。",
            "证据不足时保持观察，不封禁、不回滚；需要更多证据时记录待补的视频、反作弊或后台审计项。",
        )
    ]


def _feedback_steps(people: str, where: str, when: str) -> list[dict[str, str]]:
    return [
        _step(
            "玩家复现",
            f"联系 {people} 确认 {when} 在 {where} 的具体操作路径、命令和预期结果，使用测试账号执行同路径复现。",
            "得到可重复步骤，或确认仅影响特定玩家/权限组/后端，并保存复现日志。",
            "不能复现时收集客户端版本、时间、服务器、命令和截图，转为有负责人和截止时间的跟踪项。",
        )
    ]


def _plugin_steps(components: str, where: str) -> list[dict[str, str]]:
    return [
        _step(
            "组件检查",
            f"在 {where} 核对 {components} 的精确版本、服务端核心版本、依赖、配置和最近变更；"
            "从首个 Caused by 或插件日志前缀定位根因。",
            "确认唯一责任组件和可复现功能，依赖与版本兼容，最近变更能够解释异常。",
            "在备份后回滚最近变更或于维护窗口升级/隔离责任插件；禁止一次性更新全部插件导致证据丢失。",
        )
    ]


def _step(phase: str, check: str, expected: str, on_failure: str) -> dict[str, str]:
    return {
        "phase": phase,
        "check": check,
        "expected": expected,
        "on_failure": on_failure,
    }


def _collect(
    issues: list[dict[str, Any]],
    key: str,
    *,
    limit: int = 32,
) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for issue in issues:
        raw_values = issue.get(key) or []
        if isinstance(raw_values, str):
            raw_values = [raw_values]
        for raw in raw_values:
            value = str(raw or "").strip()
            normalized = re.sub(r"\s+", " ", value).lower()
            if not value or normalized in seen:
                continue
            seen.add(normalized)
            values.append(value)
            if len(values) >= limit:
                return values
    return values


def _plugins_from_evidence(samples: list[str]) -> list[str]:
    plugins: list[str] = []
    seen: set[str] = set()
    for sample in samples:
        for pattern in _PLUGIN_EVIDENCE_PATTERNS:
            for match in pattern.finditer(sample):
                name = _normalize_component(match.group("name"))
                key = name.lower()
                if not name or key in seen:
                    continue
                seen.add(key)
                plugins.append(name)
                if len(plugins) >= 12:
                    return plugins
    return plugins


def _configuration_files(samples: list[str]) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for sample in samples:
        for match in _CONFIG_FILE_RE.finditer(sample):
            value = match.group("path").replace("\\", "/").strip()
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            files.append(value)
            if len(files) >= 8:
                return files
    return files


def _external_services(samples: list[str]) -> list[str]:
    services: list[str] = []
    seen: set[str] = set()
    for sample in samples:
        for match in _EXTERNAL_SERVICE_RE.finditer(sample):
            host = match.group("host").strip().lower()
            if not host or host.startswith("**") or host in seen:
                continue
            seen.add(host)
            port = match.group("port")
            services.append(f"{host}:{port}" if port else host)
            if len(services) >= 8:
                return services
    return services


def _normalize_component(value: Any) -> str:
    name = str(value or "").strip().strip("[](){}:;,.")
    if not name or len(name) > 64 or "." in name:
        return ""
    name = re.sub(r"-(?:HikariPool|Hikari|Pool)$", "", name, flags=re.IGNORECASE)
    if not name or name.lower() in _IGNORED_COMPONENTS:
        return ""
    return name


def _exact_time_range(first_seen: int, last_seen: int) -> str:
    if not first_seen and not last_seen:
        return ""
    first = first_seen or last_seen
    last = last_seen or first_seen
    first_struct = time.localtime(first / 1000)
    last_struct = time.localtime(last / 1000)
    first_text = time.strftime("%Y-%m-%d %H:%M:%S", first_struct)
    if first == last:
        return first_text
    if (first_struct.tm_year, first_struct.tm_yday) == (
        last_struct.tm_year,
        last_struct.tm_yday,
    ):
        return f"{first_text} - {time.strftime('%H:%M:%S', last_struct)}"
    return f"{first_text} - {time.strftime('%Y-%m-%d %H:%M:%S', last_struct)}"


def _duration_text(first_seen: int, last_seen: int) -> str:
    if not first_seen or not last_seen or last_seen <= first_seen:
        return "单点事件"
    seconds = max(1, (last_seen - first_seen) // 1000)
    if seconds < 60:
        return f"约 {seconds} 秒"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"约 {minutes} 分 {remainder} 秒" if remainder else f"约 {minutes} 分钟"
    hours, minutes = divmod(minutes, 60)
    return f"约 {hours} 小时 {minutes} 分钟" if minutes else f"约 {hours} 小时"


def _positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _display_values(values: list[str], limit: int, fallback: str) -> str:
    if not values:
        return fallback
    text = "、".join(values[:limit])
    if len(values) > limit:
        text += f" 等 {len(values)} 项"
    return text


def _dedupe_steps(steps: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for step in steps:
        key = re.sub(r"\s+", "", step.get("check") or "").lower()[:160]
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(step)
    if len(result) <= 6:
        return result
    return [*result[:5], result[-1]]
