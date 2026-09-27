# 技术设计（Technical Design）

> 项目：netswitch
> 技术栈：Python 3（仅标准库 json/curses/ipaddress）+ iproute2/nftables（系统层）
> 设计原则：**分流规则通用化、配置声明化**——脚本/模块/配置键不硬编码 `github`；配置「声明意图」，未声明即不动作。

## 1. 总体架构

```
┌──────────────────────┐      ┌──────────────────────────────┐
│  脚本接口 scripts/    │      │  TUI 接口 src/python/tui.py  │
│  cli/tui-netswitch   │      │  (curses)                    │
└──────────┬───────────┘      └──────────┬───────────────────┘
           │          共用核心逻辑          │
           └──────────────┬───────────────┘
                          ▼
              ┌─────────────────────────┐
              │   src/python/netswitch/ │
              │  config / model /       │
              │  ip / iface / detect /  │
              │  cidrs / routing /      │
              │  apply / status / exec  │
              └───────────┬─────────────┘
                          ▼ 子进程调用（root）
        ┌─────────────────────────────────────┐
        │  ip / nft 等系统命令（可 dry-run）    │
        └─────────────────────────────────────┘
```

核心原则：**所有网络操作都收敛到 `src/python/netswitch/` 核心模块**，脚本与 TUI 只是两层薄壳，从而保证“功能一致”。`scripts/` 下的 shell 脚本是便捷入口，自动设置 `PYTHONPATH=<repo>/src/python` 后调用 CLI（`python3 -m netswitch.cli <子命令>`）或 TUI（`python3 -m netswitch.tui`）；TUI 内部直接 import 核心模块。

## 2. 目录结构

```
switch/
├── README.md                  # 软件简介与文档索引（根目录）
├── docs/
│   ├── user-manuals.md
│   ├── requirements.md
│   ├── technical.md
│   ├── plan.md
│   ├── changelog.md
│   ├── review-findings.md
│   ├── test-plan.md
│   ├── folder.md
│   └── faq.md
├── src/
│   ├── python/                        # Python 源码
│   │   └── netswitch/
│   │       ├── __init__.py        # 包标识（导入 __version__）
│   │       ├── version.py         # 版本号与构建信息（唯一来源）
│   │       ├── exec.py            # 命令执行封装：root 检查 / dry-run / 日志
│   │       ├── log.py             # 运行日志（logs/netswitch.log，轮转）
│   │       ├── model.py           # dataclass：Interface / Route / Rule / NetState
│   │       ├── config.py          # JSON 加载、校验、默认值、探测填充
│       ├── detect.py          # FR9 网络自动探测：物理网卡/IP/网关/metric/类型
│   │       ├── ip.py              # ip link/route/rule 的 JSON 解析与命令构造
│   │       ├── iface.py           # FR1 网卡开关、FR2 metric 调整
│   │       ├── cidrs.py           # 通用 CIDR 来源：URL+JSON字段 拉取/缓存/extra 合并
│   │       ├── routing.py         # 分流规则的策略路由（nftables / iprule 双后端）
│   │       ├── status.py          # FR6 状态汇总
│   │       ├── apply.py           # FR7 编排 apply / revert（metric + rules）
│   │       ├── cli.py             # argparse 命令行入口（脚本调用）
│   │       └── tui.py             # curses TUI
│   └── test/                           # 测试代码
│       ├── test_config.py
│       ├── test_detect.py
│       ├── test_ip.py
│       ├── test_iface.py
│       ├── test_cidrs.py
│       ├── test_routing.py
│       ├── test_apply.py
│       ├── test_tui.py
│       ├── test_version.py
│       ├── test_cli.py
│       └── test_log.py
├── scripts/                           # shell 便捷入口，调用 src/python 的程序
│   ├── install.sh            # 一次性安装（装依赖/生成配置/预检，可选 --with-systemd）
│   ├── release.sh            # 发布正式版（dev→master，打 tag，自动递增 dev 版本）
│   ├── check-version.sh      # 版本一致性核对
│   ├── check-branch.sh       # 分支策略核对（禁 master 直接提交）
│   ├── install-git-hooks.sh  # 安装版本化 git 钩子
│   ├── git-hooks/            # pre-commit / pre-push 钩子
│   ├── preflight.sh           # 高危操作前预检（只读）
│   ├── detect.sh              # 重新探测网络并生成配置（调用 CLI detect）
│   ├── cli-netswitch.sh       # CLI 统一入口：调用 netswitch.cli
│   ├── tui-netswitch.sh       # TUI 入口：调用 netswitch.tui
│   ├── status.sh
│   ├── iface-up.sh
│   ├── iface-down.sh
│   ├── set-metric.sh
│   ├── rule-apply.sh          # 参数：<规则名> [--interface <网卡>]
│   ├── rule-clear.sh          # 参数：<规则名>
│   ├── apply.sh
│   └── revert.sh
├── data/config/
│   ├── config.json            # 实际配置（用户维护）
│   ├── config.example.json    # 示例
│   ├── state.json             # 原始状态记录（revert 用，运行时生成）
│   ├── cache-<规则名>.json     # 各规则 CIDR 缓存（运行时生成）
│   └── netswitch.service      # systemd 服务单元模板（install-systemd 读取）
├── logs/                      # 运行日志（netswitch.log，自动轮转）
└── requirements-dev.txt       # 测试依赖：pytest 等
```

## 3. 配置设计（data/config/config.json）

设计原则：**声明式**——`metric` 出现即调整、规则 `enabled` 即应用；后端全局唯一；派生项（缓存文件/nft set/rule 优先级）自动生成，不要求用户填写。

> JSON 不支持注释，字段含义见 3.2 节默认值表与 `docs/user-manuals.md`。

### 3.1 完整示例

```json
{
  "version": 1,
  "interfaces": [
    { "name": "enp2s0", "gateway": "192.168.2.1", "metric": 100 },
    { "name": "wlp129s0", "gateway": "192.168.1.1", "metric": 600 }
  ],
  "metrics": { "preferred": 100, "fallback": 600 },
  "routing": { "backend": "auto", "nft_table": "netswitch" },
  "rules": [
    {
      "name": "github",
      "enabled": true,
      "interface": "wlp129s0",
      "cidrs": {
        "source": "url",
        "url": "https://api.github.com/meta",
        "fields": ["git", "web", "api"],
        "ttl_hours": 24,
        "extra": []
      }
    }
  ],
  "state_file": "data/config/state.json"
}
```

> 说明：`data/config/config.example.json` **自带一条 `github` 缺省规则**（出口网卡留空）；配置中 `rules` 为空时可用 `cli-netswitch.sh seed-defaults` 写入缺省规则。

### 3.2 默认值（字段可省略）

| 字段 | 默认 | 说明 |
|------|------|------|
| `interfaces` | 自动探测物理网卡 | 探测不到时要求显式配置 |
| `interface.gateway` | 当前默认路由网关 | 从 `ip -j route` 解析 |
| `interface.gateway6` | 当前 v6 默认路由网关 | 从 `ip -6 -j route` 解析（常为 `fe80::1`） |
| `interface.metric` | 无 | 不填则 apply 不调整该网卡 |
| `metrics.preferred` | 100 | `metric primary` 给优先网卡 |
| `metrics.fallback` | 600 | `metric primary` 给其余网卡 |
| `routing.backend` | auto | `auto` → 不支持策略路由用 `mainroute`；否则有 `nft` 用 nftables，否则 iprule |
| `routing.nft_table` | netswitch | nft 表名 |
| `routing.ip_versions` | `["v4"]` | 参与分流的地址族：`v4` / `v6`（默认仅 v4，向后兼容） |
| `rule.enabled` | true | |
| `rule.interface` | 无（必填） | 空 = 该规则不生效 |
| `rule.cidrs.source` | url | |
| `rule.cidrs.extra` | 无 | 字面 IP/CIDR（交互新增时写入） |
| `rule.cidrs.domains` | 无 | 域名/通配符（如 `example.com`、`*.github.com`）：**apply 时解析**（最多 64 条） |
| `rule.cidrs.wildcard_probe` | true | 对 `*.example.com` 是否做 DNS 通配符探测（随机子域查询） |
| `rule.cidrs.ip_versions` | 无（跟随 `routing.ip_versions`） | 规则级地址族覆盖 |
| `rule.cidrs.ttl_hours` | 24 | |
| `rule.cidrs.cache_file` | `data/config/cache-<规则名>.json` | 内部自动（必须位于数据目录内） |
| `rule.table_id` | 200 + 规则序号 | 自动保证唯一；**取值限定 200..252** |
| `rule.fwmark` | 1 + 规则序号 | 自动保证唯一；**取值限定 1..0xFFFFFFFF** |
| `rule.nft_set` / `rule.rule_pref` | `<规则名>_v4` / 20000+序号 | 内部自动 |
| `state_file` | `data/config/state.json` | 必须位于数据目录内 |

> 扩展方式：新增一条规则只需在 `rules` 列表加一段（不同 `name`/`url`/`fields`/`interface`），程序无需改动；`table_id`/`fwmark` 不填会自动避让。

**取值约束与校验**（加载时硬校验，非法配置直接拒绝启动，详见 §4.7）：

| 字段 | 约束 | 理由 |
|------|------|------|
| `routing.backend` | `auto` / `nftables` / `iprule` / `mainroute` | 拼错不再静默回退 |
| `routing.nft_table` | `[A-Za-z0-9_]{1,32}` | 会被拼进 `nft -f` 脚本，防注入 |
| `rules[].name` | `[A-Za-z0-9_.-]{1,32}`，且归一化后不得重名 | set 名由规则名派生，重名会让整表失败 |
| `rules[].table_id` | 200..252 | 253/254/255 是内核保留的 default/main/local，误用会 `flush` 主表 |
| `rules[].fwmark` | 1..0xFFFFFFFF | 0 无法用于打标 |
| `rules[].cidrs.source` | `url` / `manual` | 未知来源直接报错 |
| `rules[].cidrs.domains` | 每条规则 ≤ 64 条；每段只允许字母/数字/-/_；`*.` 只能在最左侧；不得写 IP | 会参与 DNS 查询与写入 |
| `rules[].cidrs.wildcard_probe` | 布尔 | 控制是否发探测查询 |
| `routing.ip_versions` / `cidrs.ip_versions` | 只允许 `v4`/`v6`，非空且不重复 | 地址族白名单 |
| `interfaces[].gateway` / `gateway6` | 必须是 IPv4 / IPv6 地址（`gateway6` 允许 `fe80::1%eth0` 带 zone） | 避免把域名或错位的族写进 `ip route ... via` |
| `state_file` / `cache_file` | 解析后必须位于数据目录（默认 `<仓库>/data`）内 | 这些路径由 root 写入，防越权写文件（可用 `NETSWITCH_DATA_DIR` 覆盖数据目录） |

### 3.3 状态文件（state.json，运行时生成）

`apply` **首次**执行时记录每张网卡当前的默认路由（gateway/dev/metric/src）、已应用规则、后端与派生路由表号；`revert` 据此恢复。

```json
{
  "version": 1,
  "original_defaults": [
    {"dev": "enp2s0", "gateway": "192.168.2.1", "metric": 100, "src": "192.168.2.175"},
    {"dev": "wlp129s0", "gateway": "192.168.1.1", "metric": 600, "src": "192.168.1.7"}
  ],
  "applied_rules": ["github"],
  "backend": "nftables",
  "tables": [200]
}
```

要点：

- **`original_defaults` 只在首次记录，后续 apply 不覆盖**：否则第二次 apply 抓到的“原始值”已被自己改过，`revert` 会恢复不回去。
- `tables` / `backend` 用于清理“改配置/改后端之前”遗留的 `ip rule` 与派生路由表。
- `revert` 成功后删除该文件，下一次 `apply` 重新建立基线（此时抓到的就是恢复后的真实值）。
- 文件损坏 / 非 JSON 对象时按“无记录”处理并告警，不阻塞流程。

## 4. 核心机制

### 4.1 FR1 网卡开关与连接状态

```bash
ip link set <iface> down   # 关闭（内核自动移除该网卡的路由）
ip link set <iface> up     # 开启（DHCP/NetworkManager 自动恢复 IP 与默认路由）
```

要点：
- 支持 1~N 张物理网卡（数量不固定）；只做链路层开关，不碰 IP 配置，恢复依赖 DHCP。
- 保护：目标为「仅剩的生效物理网卡」（唯一承载默认路由者）时**禁止关闭**（`--force` 为显式应急覆盖）。
- `apply` 不改变网卡开关状态（开关是显式的一次性操作，非配置项）。

**连接状态判定**（供 `status`/TUI 显示）：

| 状态 | 判定 | 显示 |
|------|------|------|
| 已连接 | `operstate=UP` 且（有线：无 `NO-CARRIER`）且有 IPv4 | 绿 ● UP |
| 未插网线 | 有线且 flags 含 `NO-CARRIER` | 黄 ○ 未插网线 |
| 未有连接 | `operstate=DOWN/DORMANT`（无线未关联或已关闭） | 灰 ○ 未有连接 |

- 有线/无线以 `/sys/class/net/<if>/wireless` 是否存在判定，在 TUI 用不同颜色/图标高亮。

### 4.2 FR2 metric 调整

只重建**默认路由**（不碰 on-link 路由，避免破坏直连子网）。以把 `enp2s0` metric 从 100 改为 200 为例：

```bash
ip route del default via 192.168.2.1 dev enp2s0
ip route add default via 192.168.2.1 dev enp2s0 metric 200
```

- 网关来自配置或当前路由探测；先读后删再建。
- **该网卡的所有默认路由都会被清掉**（可能因不同 metric / 不同来源有多条），避免残留重复默认路由。
- `metric set <iface> <n>`：设为指定值（运行时一次性；**`<n>` 必填**，缺失时 CLI 直接报错退出 2）。
- `metric primary <iface>`：该网卡设为 `metrics.preferred`（默认 100），其余物理网卡设为 `metrics.fallback`（默认 600，多张时可能同值）。若目标网卡不在配置的 `interfaces` 中，会按探测结果自动补齐，保证它确实被设为 preferred（否则会把其余网卡全压成 fallback）。
- `revert` 恢复时若原始路由没有 metric（`metric` 字段缺失），则重建不带 metric 的默认路由（`metric=None`），不会凭空塞一个值。
- 记录改动前的 `(gateway, dev, metric)` 到 state 文件，供 revert。
- 网关探测依赖网卡当前存在默认路由；若网卡当前 DOWN 或无路由，须在配置中显式提供 `gateway`。

### 4.3 FR3 分流规则（策略路由）

通用思路：为每条规则建一张独立路由表（默认走目标网卡网关），再把“匹配到的流量”投递到这张表。主路由完全不受影响。以规则 `github`（出口 `wlp129s0`，网关 `192.168.1.1`，table 200，fwmark 1）为例：

**路由表初始化**（两后端共用）：

```bash
# 独立表：默认走无线网关；补直连子网路由保证网关可达
ip route add default via 192.168.1.1 dev wlp129s0 table 200
ip route add 192.168.1.0/24 dev wlp129s0 proto kernel scope link src 192.168.1.7 table 200
```

**后端 A：nftables（默认，规则数量恒定，推荐多规则场景）**

```bash
# 整表原子加载（nft -f）：由配置生成完整 ruleset 并替换，天然幂等
nft -f - <<'EOF'
table inet netswitch {
  set github_v4 { type ipv4_addr; flags interval; elements = { <cidr1>, <cidr2>, ... } }
  chain prerouting { type filter hook prerouting priority mangle; policy accept;
    ip daddr @github_v4 meta mark set 0x1
  }
  chain output { type route hook output priority mangle; policy accept;
    ip daddr @github_v4 meta mark set 0x1
  }
}
EOF

# 标记流量查询独立路由表
ip rule add fwmark 0x1 lookup 200 pref 20000
```

> 表与两条链只建一次；每条规则新增一个 set 和两条 rule（`prerouting` + `output`）。整表由配置一次性生成并原子替换，apply 天然幂等。

**后端 B：iprule（纯 iproute2，零额外依赖，回退方案）**

```bash
for cidr in <cidrs...>; do
  ip rule add to "$cidr" lookup 200 pref 20000
done
```

说明：
- `to <cidr>` 目标匹配同时覆盖本机流量与转发（容器）流量，无需额外打标。
- 缺点：CIDR 多时产生较多 `ip rule`；nftables 后端每条规则只需 1 条标记规则。

**后端 C：mainroute（主表明细路由，回退方案）**

在**不支持策略路由**的环境（内核缺 `CONFIG_IP_MULTIPLE_TABLES`、受限容器/gVisor，报 `RTNETLINK answers: Operation not supported`），改用主表按目标网段加明细路由，无需多路由表与 `ip rule`：

```bash
for cidr in <cidrs...>; do
  ip route replace "$cidr" via <网关> dev <出口网卡>
done
```

- `backend: auto` 时，若探测到策略路由不可用，**自动使用 mainroute**。
- 明细路由带 `proto 200` 标记（`iproute2` 只接受 1..255 的数值 proto）。清理时按**签名**识别自己的路由：只删“主表中确实存在、`proto = 200`、且出口网卡匹配”的条目，命令形如 `ip route del <cidr> proto 200 dev <出口网卡> table main`。**不会**碰到内核直连路由、他人的静态路由（`proto static`）或 VPN 路由（旧实现的无条件 `ip route del <cidr> table main` 会误删它们，已修正）。
- 重建使用 `ip route replace`，且只清理“不再需要”的条目，避免“清空再重建”的瞬时断流。
- 局限：仅按目标网段分流；目标网段多则主表条目较多；不支持按域名等。

**撤销（revert / rule clear）**

```bash
# 后端 A：重新生成不含该规则的整表并原子替换（或删空后 drop 表）
nft delete table inet netswitch
# 后端 B：逐个删除 ip rule to <cidr> lookup 200
ip rule del fwmark 0x1 lookup 200 pref 20000
ip route del default via 192.168.1.1 dev wlp129s0 table 200
ip route del 192.168.1.0/24 dev wlp129s0 table 200
```

> 多规则：每条规则独立的 `table_id`/`fwmark`/`nft_set`。

### 4.3.1 应用与清理的原子性（重要）

| 机制 | 做法 | 好处 |
|------|------|------|
| 先校验后改动 | `routing.preflight()` 先检查：出口网卡存在、网关可解析、CIDR 合法、`iprule` 后端所需 `ip rule` 数量不超上限（`RULE_PREF_MAX_COUNT=12000`，避免撞内核默认 `pref 32766`） | 失败时**零改动**，不会“清完才发现配置错” |
| 一次拉取 | 同一次 apply 中每个 CIDR 源只拉取一次，并把结果传给清理与建表 | 避免同一 URL 请求 3~4 次、以及 set 与实际规则来自不同批次数据 |
| 失败回滚 | 重建过程中出错则 best-effort 再清理一次并抛出 | 不留半成品 `ip rule`/路由表 |
| 只删自己的产物 | nft 表名 / `proto 200` 主表路由 / `pref ∈ [20000, 32000)` 且表号在派生区间的 `ip rule` | 不误伤系统与他人的路由、规则 |
| 声明式重建 | 无论调用 `apply` 还是 `rule apply <name>`，都按**配置声明的全部规则**重建 | 单独应用一条规则不会把其它规则清掉（旧行为会） |
| 清理历史残留 | `revert`/清理会带上 state 里的历史表号，并扫描上述 `pref` 区间 | 规则被删除/改名/换表号后也能清干净；内核保留表 253/254/255 永不 `flush` |

### 4.4 容器出网流量覆盖（强制要求）

分流规则必须同时覆盖**主机本机**与**主机上所有容器（网络命名空间）**的对外流量。两条覆盖路径：

| 流量来源 | 覆盖机制 | 匹配/打标钩子 |
|----------|----------|----------------|
| 主机本机进程 | OUTPUT 链打标（`type route`）→ 二次路由决策 | nftables `output` / `ip rule to <cidr>` |
| 容器转发流量 | PREROUTING 链打标 → 路由决策 → POSTROUTING NAT | nftables `prerouting` / `ip rule to <cidr>` |

容器出网完整链路：

```
容器 netns → veth → 主机网桥（br-*/lzc-br-*/docker0）
          → 主机 PREROUTING（打标 fwmark / 匹配 to <cidr>）
          → 路由决策（ip rule fwmark → 独立路由表 → 指定网卡）
          → POSTROUTING MASQUERADE（源地址改写为指定网卡 IP）
          → 物理网卡发出
```

关键点与注意事项：

1. **转发打标**：nftables 的 `prerouting` 链（`priority mangle`）在路由决策前作用于所有进入主机的数据包，因此容器转发流量会被打标并进入独立路由表。
2. **NAT 源地址**：Docker/LXC 默认 `MASQUERADE` 规则按“出接口”自动选源 IP，被分流到指定网卡的容器流量会自动改写成该网卡 IP。⚠️ 若某容器网络存在 `-o <特定网卡> -j MASQUERADE` 的显式规则，需确认其覆盖目标出口网卡。
3. **rp_filter**：容器流量经网桥进入、从另一网卡发出，需确保反向路径过滤为宽松模式（`net.ipv4.conf.all.rp_filter=0` 或 `=2`，主流发行版默认满足；否则转发包可能被丢弃）。
4. **验证手段**：
   - `ip route get <目标IP> from <容器网段IP> iif <网桥名>` 应显示走指定网卡。
   - 容器内 `curl` 规则目标站点，出口公网 IP 应为指定网卡 IP。
   - `conntrack -L`（或 nft/iptables 规则计数）确认连接被 NAT 到指定网卡。

### 4.5 CIDR 来源获取与缓存（通用）

- `cidrs.source: url` 时请求 `cidrs.url`，从返回 JSON 中提取 `cidrs.fields` 指定的字段里的 IPv4 CIDR。
- 结果缓存到 `data/config/cache-<规则名>.json`，超过 `ttl_hours` 才重新拉取；拉取失败时回退到缓存。
- 与 `cidrs.extra` 合并后**规范化并去重**：用 `ipaddress.ip_network(..., strict=False)` 统一为网络地址（`192.168.1.5/24` → `192.168.1.0/24`），裸地址按 `/32` 处理；非法项（域名、`1.2.3.4/999`、IPv6）一律丢弃并告警，绝不喂给 `nft`/`ip rule`。
- 下载加固：响应体量上限 8MB、必须是 JSON 对象、`fields` 不得为空，否则报错并走缓存回退。
- 已知局限：流量目标新增网段时需刷新缓存或手动补充 `extra`；初始 `github` 规则的权威来源是 `https://api.github.com/meta`。

### 4.6 FR9 网络自动探测与配置刷新

用于把程序迁移到新机器、或网络环境变化后重新生成“机器相关”的配置（不写死）。流程：

1. 枚举接口：`ip -j link`，排除虚拟接口（`lo`/`veth*`/`br-*`/`lzc-*`/`docker*`/`tun*`/`tap*`/`virbr*`/`wg*`/`ppp*`/`dummy*`/`macvlan*`/`tailscale*` 等）与 **VLAN/别名子接口**（名字含 `.` 的 `enp2s0.100`）；随后优先用 **sysfs 判定**（`/sys/class/net/<if>/device` 存在 = 真实 PCI/USB 设备），再回退命名前缀（`en`/`eth`/`wl`/`wlan`/`ww`）与“`link_type=ether` 且无 master/link-netns”兜底。`bond0` 这类无 `device` 节点的聚合设备走兜底分支（仍可作为出口），其 slave 因带 `master` 被排除。
2. 判类型：存在 `/sys/class/net/<if>/wireless` → 无线（并读当前 SSID：`iw dev <if> link`，回退 `iwgetid -r`），否则有线（TUI 高亮区分）。
3. 读 IP：`ip -j -4 addr show <if>`。
4. 读连接状态：`operstate`/`NO-CARRIER` → 已连接 / 未插网线 / 未有连接（见 §4.1）。
5. 读默认路由：`ip -j route show default`，按 `dev` 匹配；同一网卡多条默认路由时取 **metric 最小**者（与 `primary_interface` 语义一致）。
6. 生成 `interfaces` 配置片段（name/type/gateway/metric；类型与连接状态供 status/TUI 显示）。
7. 合并策略：默认 **dry-run**（只打印拟生成 JSON）；`--write` 时**仅更新 `interfaces` 段**，保留 `rules`/`metrics`/`routing`/`state_file` 等；若规则引用的网卡已不存在则告警（不自动删除）。

CLI 与 TUI 均触发同一探测逻辑（`detect.py`）。`detect` 输出中的 `*` 标记表示该网卡无 sysfs `device` 节点（容器/受限环境，或 bond 等聚合设备），物理性按命名推断。

### 4.7 配置校验与路径约束

配置里的值会被拼进 `nft -f` 脚本、`ip` 命令参数或直接用于文件写入，因此全部白名单校验（见 §3.2 表），非法配置在 `config.load()` 阶段直接报错，不进入任何写操作：

- **标识符**：`routing.nft_table` 与 `rules[].name` 限制字符集与长度；规则名归一化后在 nft 中重名也报错（`a-b` 与 `a.b` 都变成 `a_b_v4`）。
- **数值**：`table_id` ∈ 200..252（避开内核保留表 253/254/255，防止 `ip route flush table 254` 清空主表）；`fwmark` ∈ 1..0xFFFFFFFF；`metrics.*` 非负；`ttl_hours` 非负。
- **路径**：`state_file` 与 `cidrs.cache_file` 解析后必须位于**数据目录**（默认 `<仓库>/data`，可用 `NETSWITCH_DATA_DIR` 覆盖）内，拒绝绝对路径越界与 `..` 穿越——这两个文件是由 root 写入的，旧实现可被配置指向 `/etc/...`。相对路径统一按**仓库根**解析（与 `log.py` 一致），不再随当前工作目录漂移。
- **威胁模型**：仓库/配置目录可能由普通用户拥有，而 `apply` 由 `sudo`/systemd 以 root 运行。除上述校验外，建议 `data/config/config.json` 设为 `root:root 0600`。

### 4.8 IPv6 分流（双栈）

同一套规则可按地址族分别应用，目标是“能用 v6 就分流 v6，用不了就不影响 v4”。

**地址族来自哪里**：`rules[].cidrs.ip_versions`（无则用全局 `routing.ip_versions`，默认 `["v4"]`）。CIDR 源（URL / `extra`）现在同时保留 v4 与 v6（`cidrs._finish` 不再丢弃 v6），由 `routing` 按规则声明的族筛选。

**应用（per rule × per family 的 `FamilyPlan`）**：`preflight` 阶段为每条规则的每个地址族解析出 `(family, cidrs, gateway, src, subnet)`：

| 后端 | IPv4（原有） | IPv6（新增） |
|------|--------------|---------------|
| nftables | `set <rule>_v4 { type ipv4_addr; ... }` + `ip daddr @set meta mark ...` | `set <rule>_v6 { type ipv6_addr; ... }` + `ip6 daddr @set meta mark ...`（仍是一个 `table inet`，整表加载） |
| nftables（选表） | `ip rule add fwmark 0xN lookup T pref P` | `ip -6 rule add fwmark 0xN lookup T pref P` |
| iprule | `ip rule add to <cidr> lookup T pref P` | `ip -6 rule add to <cidr> lookup T pref P`；独立表内补 `ip -6 route add default via <gw6> dev <if> table T` 与 v6 直连路由 |
| mainroute | `ip route replace <cidr> via <gw> dev <if> proto 200` | `ip -6 route replace <cidr> via <gw6> dev <if> proto 200` |

**v6 网关**：优先 `interfaces[].gateway6`，否则取该网卡当前 v6 默认路由的网关（`ip -6 -j route show default`，常见 `fe80::1`；链路本地网关必须带 `dev`，我们的命令里总是带），同一网卡多条时取 metric 最小者。

**降级规则（不阻断）**：

| 情况 | 行为 |
|------|------|
| `ip.family_available(6)` 为假（内核未启用/命名空间不支持 IPv6） | 告警跳过 v6 |
| 出口网卡无 v6 网关且配置未指定 `gateway6` | 告警跳过 v6 |
| iprule/nftables 后端且 `policy_routing_supported(6)` 为假（缺 `CONFIG_IPV6_MULTIPLE_TABLES`） | 告警跳过 v6 |
| 上述任一情况发生在**只声明 v6** 的规则上 | 该规则整体跳过（告警），其它规则不受影响 |
| `mainroute` 后端 | 不依赖策略路由，上述第三条不适用 |

**清理**：`main_table_routes(family)` 分别读两族主表，只删 `proto 200` + 出口网卡匹配的条目（`ip -6 route del ... proto 200 ...`）；`ip rule` 与派生路由表按 `pref ∈ [20000,32000)` + 派生表号逐族清理（`ip -6 rule del` / `ip -6 route flush table N`）。因是两套独立规则库，优先缀计数器在两族间共用以简化实现，但“上限校验”按族分别计算。

**已知局限**：v6 分流同样只能按网段匹配（不做域名）；上游给 IPv6 记录时可能同时给 v4，两者可独立开关；若只有链路本地地址（无全局 v6 地址），则不参与分流（不能作为源地址）。

### 4.9 域名规则（FR11）

为降低使用门槛，新增/删除规则可以直接用域名（不必自己查网段）。

**为什么不在配置时解析**：域名对应的 IP 会变（CDN、滚动发布），在 **apply 时**解析才能拿到当下的地址；
配置里保留的是**域名本身**（可读、可改、可审阅），解析结果只进缓存。

**输入分类**（`cidrs.classify_target` / `parse_targets`）：

| 输入 | 归类 | 结果 |
|------|------|------|
| `1.2.3.4` / `2001:db8::1` | CIDR | 主机路由 `/32` / `/128` |
| `10.0.0.0/8` / `2001:db8::/32` | CIDR | 归一化后原样 |
| `example.com` / `*.github.com` | 域名 | 校验并转小写 |
| `https://github.com/org/repo` | 域名 | 提取主机名 `github.com` |

无法识别的项**直接报错**（交互场景不静默丢弃）；单条规则最多 64 个域名。

**解析（`cidrs.resolve_domains`）**：`socket.getaddrinfo` 取 A + AAAA → 主机 CIDR；
`*.example.com` = 解析 apex + （`wildcard_probe` 开启时）对随机标签 `netswitch-wildcard-probe-<随机>.example.com` 发一次查询，
只有该域真的配置了 DNS 通配符记录才会返回地址。

> **重要局限**：DNS 没有“枚举子域”的接口，所以 `*.github.com` **不等于** github.com 的所有子域。
> 需要精确覆盖时，把具体子域（`api.github.com`、`gist.github.com` …）一并写进 `domains`。
> 想“动态跟随真实解析结果”需要 DNS 代理/nftset 类方案（已在 review-findings 记录为备选，未采用）。

**缓存（`cache-<规则名>.json` 结构 v2）**：

```json
{
  "fetched_at": 0, "url_cidrs": [],
  "domains_at": 0, "domain_patterns": ["*.github.com"], "domain_cidrs": ["140.82.112.0/20"],
  "cidrs": ["140.82.112.0/20"]
}
```

- URL 与域名两部分各有时间戳、各自复用 `cidrs.ttl_hours`；
- 域名列表变了（增删/改名）会强制重新解析；
- DNS 失败时回退 `domain_cidrs` 并告警；旧版缓存（只有 `fetched_at` + `cidrs`）仍能作为 URL 部分使用。

**写入路径（`config.add_rule` / `remove_rule`）**：

- 两个函数**只改配置文件**，不调用任何网络命令；写入前校验（规则名/网段/域名/出口网卡/地址族），
  写后用 `config.load()` 重新校验，**失败则回滚文件原文**（不让 `apply` 读到坏配置）；
- 不写 `table_id`/`fwmark`/`cache_file`：这些仍由加载时自动派生（保持“派生项内部自动”的设计）；
  因此删掉中间一条规则后其余规则的派生表号会前移，`apply` 时靠签名扫描清理旧表（§4.3.1）；
- TUI/CLI 两侧共用同一套函数，保证行为一致（FR5）。

**界面与命令**：TUI `n`（新增）/ `x`（删除）均**只写配置**；CLI `rule add` / `rule remove` 同理。
与之相对，`e`（选出口）/`c`（撤销分流）/`p`（应用）/`a`（应用全部）/`r`（撤销全部）会真正作用到网络。

## 5. 安全与健壮性

| 措施 | 说明 |
|------|------|
| root 检查 | `os.geteuid()==0`，否则报错退出 |
| dry-run | 所有破坏性命令支持 `--dry-run`，只打印不执行；**只读查询（`ip -j ... show`）即使 dry-run 也真实执行**，以便准确展示“将删除/将添加什么”；能力探测已改为只读，dry-run 不改动系统 |
| 操作确认 | 网卡开关 / 转换 / 规则改写等破坏性操作需确认（TUI 提示 `y`；CLI `-y/--yes` 跳过，非交互需 `-y`） |
| 最后网卡保护 | 仅一张网卡生效（唯一承载默认路由）时禁止关闭它；`--force` 为显式应急覆盖 |
| 只删自己的产物 | 清理按签名识别：nft 表名、主表 `proto 200` 明细路由（且出口网卡匹配）、`pref ∈ [20000, 32000)` 的 `ip rule` 与派生表号；不碰内核直连路由/他人静态路由 |
| 双栈降级 | 某地址族不可用（内核未启用 v6 / 无 v6 网关 / 无 v6 策略路由）时**告警并跳过该族**，其余族照常应用；不会因此整体失败 |
| 出口网卡检查 | 出口网卡**不存在** → 预检直接拒绝（零改动）；网卡不是物理网卡或**当前未连接** → 告警并仍按配置应用（无线可能稍后才关联） |
| 先校验后改动 | `preflight()` 全部通过才清理重建；网卡不存在、网关不可解、CIDR 非法、`ip rule` 超上限均直接拒绝，且**零改动** |
| 失败回滚 | 重建中出错则 best-effort 清理已建产物后抛出，不留半成品状态 |
| 配置白名单 | 标识符/数值/路径全部校验（§3.2、§4.7），未知 `backend` 报错而非静默回退 |
| 命令注入防护 | 命令均以参数列表形式 `subprocess` 执行（无 shell）；nft 脚本内容由校验过的标识符拼成；下载体量上限 8MB |
| 幂等 | apply 按配置声明重建（nft 整表替换 / 主表 `replace` / `ip rule` 先清后建），重复执行结果一致 |
| 原始状态记录 | state.json 记录首次 apply 前的状态，`revert` 恢复；二次 apply 不覆盖基线 |
| 命令原子报告 | 逐条执行并记录成功/失败，失败不静默；CLI 顶层兜底异常，只给人话 + 日志 traceback |

## 6. CLI 子命令（脚本接口）

```
cli-netswitch.sh status                            # 显示状态
cli-netswitch.sh detect [--write] [--dry-run]      # 重新探测网络并生成/刷新配置（默认只打印）
cli-netswitch.sh seed-defaults                     # rules 为空时写入缺省规则
cli-netswitch.sh iface up <name>                   # 开启网卡
cli-netswitch.sh iface down <name> [--force]       # 关闭网卡
cli-netswitch.sh metric set <name> <n>             # 设置 metric
cli-netswitch.sh metric primary <name>             # 设为优先网卡（metrics.preferred/fallback）
cli-netswitch.sh rule apply <rule> [--interface <iface>] [--dry-run]   # 应用某条分流规则
cli-netswitch.sh rule clear <rule> [--dry-run]                          # 撤销某条分流规则（写入配置）
cli-netswitch.sh apply [--dry-run] [--force]       # 按配置应用全部（metric + rules）
cli-netswitch.sh revert [--dry-run]                # 撤销全部改动
cli-netswitch.sh install-systemd                   # 生成并启用 systemd 服务
tui-netswitch.sh                                   # 启动 TUI（scripts 便捷入口）
```

- `<rule>` 为配置中 `rules[].name`（如 `github`）；`--interface` 为一次性运行时覆盖（不写回配置）。
- `rule apply <rule>`：语义为“**按配置重建全部规则**（声明式）”；`<rule>` 仅用于确认提示。这样单独应用一条规则不会把其它规则清掉。
- `rule clear <rule>` 会清空该规则在 `config.json` 中的 `interface`（持久撤销）并重建策略路由。
- `metric set <name> <n>`：`<n>` 必填，缺失时报参数错误（退出码 2）。
- 破坏性操作（网卡开关、metric 转换、规则改写、apply/revert）默认**需确认**（交互提示 `[y/N]`）；`-y/--yes` 或环境变量 `NETSWITCH_YES=1` 跳过；非交互且未跳过则拒绝。
- **顶层选项 `--config` 必须写在子命令之前**：`-m netswitch.cli --config <path> apply`（systemd 单元与安装脚本均按此写法）。
- `detect` 默认只读（打印拟生成配置），加 `--write` 才写回，且仅更新 `interfaces` 段。
- `scripts/` 下 shell 脚本自动设置 `PYTHONPATH=<repo>/src/python` 后调用 CLI/TUI。
- `rule-apply.sh <规则名> [--interface 网卡]`、`rule-clear.sh <规则名>` 为规则操作的便捷包装。

## 7. TUI 设计（curses，零依赖）

纯键盘单屏界面（无鼠标、无第三方库）：

- **标题栏**：显示当前主机名（**蓝色**）：`netswitch · <主机名> · 网卡切换控制台`。
- **启动即拉取 + 自动刷新**：进入即拉取实时网卡信息（数量 1~N、有线/无线、连接状态、IP、metric、默认路由）；界面每 ~1.5s 自动刷新，`f` 或 `F5` 手动刷新；底部状态栏显示“最后刷新 yyyyMMdd HH:mm:ss +ZZZZ”。因此开启网卡后 DHCP 获取 IP、链路变化会自动显示。
- **刷新开销控制**：一次刷新周期内只探测一次网卡，探测结果直接传给 `config.load(probe_map=...)`；`mainroute` 后端下只 dump 一次主表并供所有规则复用（旧实现每 1.5s 会重复跑多轮 `ip` 命令、每条规则各 dump 一次主表）。
- **错误展示**：状态栏只有一行，长错误（如“策略路由不受支持”多行提示）改用**错误弹层**完整折行展示，按任意键关闭；执行前状态栏先显示“执行中…”，避免拉取 CIDR 时看似卡死。
- **配置非法也不崩溃**：配置校验失败时 TUI 照常打开并在状态栏/规则区上方显示原因（不因一个字段写错就退不到界面）。
- **导航**：`↑`/`↓` 在所有条目（网卡在前、规则在后）间移动选中项；数字 `1`…`N` 直接选网卡；`g` 切换规则；`Enter` 对**网卡**执行默认动作（**设为主网卡**）；选中规则时 `Enter` 仅提示，不直接执行。
- **网卡区颜色语义**：
  - **分流生效网卡**（某条已应用规则的出口）→ **黄色**
  - **主网卡**（有默认路由且 metric 最小）→ **绿色**
  - **其他可用网卡** → **蓝色**
  - **被禁止 / 不可用**（管理关闭 admin-down，或未插网线）→ **红色**
  - 数字 `1`…`N` 选中后：`o` 关闭/开启（按**管理状态**判断）、`m` 设为主网卡、`t` 改 metric。
  - 无线网卡额外显示当前 **SSID**（未连接显示 `-`；由 `iw dev <if> link` 获取，回退 `iwgetid -r`）。
  - 网卡有 IPv6 地址时额外显示 `6=<v6 地址>`；规则的「生效网卡」在参与 v6 分流时附加 `(v4+v6)` 标记。
- **规则区**：列出配置中的每条规则，显示**生效网卡**与是否**已应用**；规则**生效**时其「生效网卡」显示为**黄色**。`g` 切换选中规则、`e` 选生效网卡、`p` 应用、`c` 撤销。
  - `e`：弹出网卡序号列表，选择后**写回 `config.json`**（持久化）并重建策略路由；`0` = 不分流。
  - `c`：撤销该规则的分流（清空其 `interface` 并**写回 `config.json`**）。
- **底部输入行**：metric 数值、网卡序号、确认（`y/N`）等通过底部提示行输入，无弹窗。
- **所有写操作都需确认**：关闭/开启网卡、设为主网卡、改 metric、规则改生效网卡、应用/撤销规则、应用全部、撤销全部，均在底部输入 `y` 确认。

> 网卡与规则分开选择：数字仅用于选网卡，规则用 `g` 切换，避免混淆。

### 7.1 快捷键总表

| 按键 | 作用 |
|------|------|
| `↑` / `↓` | 上下移动选中项（网卡/规则） |
| `Enter` | 对选中网卡执行默认动作（设为主网卡）；选中规则时仅提示 |
| `Esc` | 返回主页面并刷新 |
| `1`…`N` | 选中第 1…N 张网卡 |
| `o` / `m` / `t` | 关闭开启 / 设为优先 / 改 metric（选中网卡后） |
| `g` | 切换选中的分流规则 |
| `e` | 为选中规则选择生效网卡（写回 config.json） |
| `p` / `c` | 应用 / 撤销选中规则 |
| `d` / `a` / `r` / `f` / `F5` / `q` | 重新探测 / 应用全部 / 撤销 / 刷新（`f`/`F5`）/ 退出 |
| `y` / `N` | 底部输入确认 / 取消 |

> 字母键**不区分大小写**；`Esc` 返回主页面并刷新；`q` 或 `Ctrl+C` 退出；`f`/`F5` 刷新（已显式启用 `keypad`，F5 可用）。未绑定的可打印键会提示。
> 输入/确认时：`Esc` 取消当前输入，`Ctrl+C` 退出程序。

## 8. systemd 集成

```ini
# data/config/netswitch.service（模板，含 __REPO_ROOT__ 占位）
[Unit]
Description=netswitch: apply network switch config
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
# 相对路径（state.json / cache-*.json）按仓库根解析，因此固定工作目录
WorkingDirectory=__REPO_ROOT__
Environment=PYTHONPATH=__REPO_ROOT__/src/python
# 顶层 --config 必须在子命令之前
ExecStart=/usr/bin/python3 -m netswitch.cli --config __REPO_ROOT__/data/config/config.json apply --yes

[Install]
WantedBy=multi-user.target
```

`install-systemd` 将服务写入 `/etc/systemd/system/` 并 `systemctl enable`；写入前会用同一套 argparse 解析 `ExecStart`，命令行不合法直接报错（避免“装得上、开机不生效”）。开机联网后自动应用配置（metric + 全部分流规则）。

## 9. 依赖

| 依赖 | 用途 | 是否必需 |
|------|------|----------|
| `ip`（iproute2） | 路由/网卡操作 | 必需 |
| 内核策略路由（`CONFIG_IP_MULTIPLE_TABLES`） | 自定义路由表 + `ip rule`（分流必需） | 必需（分流功能） |
| IPv6 支持（启用 IPv6 + `CONFIG_IPV6_MULTIPLE_TABLES`） | v6 分流（`ip -6 rule` / `ip -6 route ... table N`） | 可选（仅 v6 分流需要；缺失时自动跳过 v6） |
| `nft`（nftables） | 后端 A | 可选，缺省回退 iprule |
| Python 3.9+（标准库） | 运行（json / curses / ipaddress） | 必需 |

> 无第三方 Python 运行时依赖（不依赖 PyYAML、textual）。
> 若内核未启用 `CONFIG_IP_MULTIPLE_TABLES`（或在受限容器/gVisor 中运行），`ip route ... table N` / `ip rule` 会报 `RTNETLINK answers: Operation not supported`，此时**无法分流**（可在 `preflight.sh` 的「策略路由能力」项确认）。
> IPv6 同理对应 `CONFIG_IPV6_MULTIPLE_TABLES`：不影响 v4 分流，只是 v6 部分会被跳过并告警（`mainroute` 后端不依赖多路由表，即使 v6 策略路由不可用也能做 v6 明细路由分流）。

## 10. 错误处理

- 子命令返回非 0：收集 stderr，抛出带命令上下文的 `ExecError`。
- 拉取 CIDR 源失败：告警并回退缓存/`extra`；非法 CIDR 丢弃并告警。
- 配置缺失/非法：启动时校验并给出字段级错误提示（TUI 也不退出，直接显示原因）。
- 分流应用：先 `preflight` 再改动；失败回滚清理；`apply` 中 metric 失败会继续执行规则并汇总报告。
- CLI 顶层：已知错误（`ExecError`/`PermissionError`/`RuntimeError`/`ValueError`）给人话提示；其余异常记入日志并返回 `TypeError: ...（详见日志）`，不再甩 traceback。
- 非 root 下策略路由能力为“未知”（`None`）：`status` 会明确提示，不会谎报支持/不支持。

## 11. 测试策略

详见 [test-plan.md](test-plan.md)。

## 12. 日志

- 目录选择顺序：`setup(log_dir)` 参数 → `NETSWITCH_LOG_DIR` → `<仓库>/logs` → `$XDG_STATE_HOME/netswitch/logs` → 系统临时目录。
  - 仓库内 `logs/` 若被 root 创建（install/apply 用 `sudo` 跑过），普通用户不可写，此时**自动回退**并在 stderr 提示一次（旧实现静默降级为“不写日志”）。
  - `status` 会打印当前日志文件路径。
- 文件：`netswitch.log`，按大小轮转（2MB × 5 个）。
- 级别：默认 INFO（记录写类命令与高层动作）；`NETSWITCH_LOG_LEVEL=DEBUG` 可包含只读查询。
- 内容：`exec.run` 记录每条系统命令（`OK`/`FAIL`/`DRY-RUN`）；apply/revert、网卡开关、metric、策略路由、配置写回、CIDR 拉取、CLI/TUI 启动均有记录；启动行含版本、主机名与**发起用户**（`SUDO_USER` 优先）。
