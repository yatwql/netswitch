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
| `interface.metric` | 无 | 不填则 apply 不调整该网卡 |
| `metrics.preferred` | 100 | `metric primary` 给优先网卡 |
| `metrics.fallback` | 600 | `metric primary` 给其余网卡 |
| `routing.backend` | auto | 有 `nft` 用 nftables，否则 iprule |
| `routing.nft_table` | netswitch | nft 表名 |
| `rule.enabled` | true | |
| `rule.interface` | 无（必填） | 空 = 该规则不生效 |
| `rule.cidrs.source` | url | |
| `rule.cidrs.ttl_hours` | 24 | |
| `rule.cidrs.cache_file` | `data/config/cache-<规则名>.json` | 内部自动 |
| `rule.table_id` | 200 + 规则序号 | 自动保证唯一 |
| `rule.fwmark` | 1 + 规则序号 | 自动保证唯一 |
| `rule.nft_set` / `rule.rule_pref` | `<规则名>_v4` / 20000+序号 | 内部自动 |
| `state_file` | `data/config/state.json` | |

> 扩展方式：新增一条规则只需在 `rules` 列表加一段（不同 `name`/`url`/`fields`/`interface`），程序无需改动；`table_id`/`fwmark` 不填会自动避让。

### 3.3 状态文件（state.json，运行时生成）

`apply` 前记录每张网卡当前的默认路由（gateway/dev/metric/src）与已应用的分流规则；`revert` 据此恢复。

```json
{
  "version": 1,
  "original_defaults": [
    {"dev": "enp2s0", "gateway": "192.168.2.1", "metric": 100, "src": "192.168.2.175"},
    {"dev": "wlp129s0", "gateway": "192.168.1.1", "metric": 600, "src": "192.168.1.7"}
  ],
  "applied_rules": ["github"]
}
```

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
- `metric set <iface> <n>`：设为指定值（运行时一次性）。
- `metric primary <iface>`：该网卡设为 `metrics.preferred`（默认 100），其余物理网卡设为 `metrics.fallback`（默认 600，多张时可能同值）。
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

**撤销（revert / rule clear）**

```bash
# 后端 A：重新生成不含该规则的整表并原子替换（或删空后 drop 表）
nft delete table inet netswitch
# 后端 B：逐个删除 ip rule to <cidr> lookup 200
ip rule del fwmark 0x1 lookup 200 pref 20000
ip route del default via 192.168.1.1 dev wlp129s0 table 200
ip route del 192.168.1.0/24 dev wlp129s0 table 200
```

> 多规则：每条规则独立的 `table_id`/`fwmark`/`nft_set`；`rule clear <name>` 只清理该规则对应的 set、`ip rule` 与路由表条目。

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
- 与 `cidrs.extra` 合并后去重。
- 已知局限：流量目标新增网段时需刷新缓存或手动补充 `extra`；初始 `github` 规则的权威来源是 `https://api.github.com/meta`。

### 4.6 FR9 网络自动探测与配置刷新

用于把程序迁移到新机器、或网络环境变化后重新生成“机器相关”的配置（不写死）。流程：

1. 枚举接口：`ip -j link`，排除虚拟接口（`lo`/`veth*`/`br-*`/`lzc-*`/`docker*`/`tun*`/`tap*`/`virbr*` 及 Point-to-Point 等），结合命名前缀（`en`/`eth`/`wl`/`wlan`/`ww`）识别物理网卡。
2. 判类型：存在 `/sys/class/net/<if>/wireless` → 无线（并读当前 SSID：`iw dev <if> link`，回退 `iwgetid -r`），否则有线（TUI 高亮区分）。
3. 读 IP：`ip -j -4 addr show <if>`。
4. 读连接状态：`operstate`/`NO-CARRIER` → 已连接 / 未插网线 / 未有连接（见 §4.1）。
5. 读默认路由：`ip -j route show default`，按 `dev` 匹配取 `gateway`/`metric`。
6. 生成 `interfaces` 配置片段（name/type/gateway/metric；类型与连接状态供 status/TUI 显示）。
7. 合并策略：默认 **dry-run**（只打印拟生成 JSON）；`--write` 时**仅更新 `interfaces` 段**，保留 `rules`/`metrics`/`routing`/`state_file` 等；若规则引用的网卡已不存在则告警（不自动删除）。

CLI 与 TUI 均触发同一探测逻辑（`detect.py`）。

## 5. 安全与健壮性

| 措施 | 说明 |
|------|------|
| root 检查 | `os.geteuid()==0`，否则报错退出 |
| dry-run | 所有破坏性命令支持 `--dry-run`，只打印不执行 |
| 操作确认 | 网卡开关 / 转换 / 规则改写等破坏性操作需确认（TUI 提示 `y`；CLI `-y/--yes` 跳过，非交互需 `-y`） |
| 最后网卡保护 | 仅一张网卡生效（唯一承载默认路由）时禁止关闭它；`--force` 为显式应急覆盖 |
| 出口网卡检查 | 规则出口网卡为 DOWN 时告警并阻止（可 `--force`） |
| 幂等 | apply 按配置重建（nft 整表替换 / ip rule 先清后建），重复执行结果一致 |
| 原始状态记录 | state.json 记录 apply 前状态，revert 恢复 |
| 命令原子报告 | 逐条执行并记录成功/失败，失败不静默 |

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
- `rule clear <rule>` 会清空该规则在 `config.json` 中的 `interface`（持久撤销）并重建策略路由。
- 破坏性操作（网卡开关、metric 转换、规则改写、apply/revert）默认**需确认**（交互提示 `[y/N]`）；`-y/--yes` 或环境变量 `NETSWITCH_YES=1` 跳过；非交互且未跳过则拒绝。
- `detect` 默认只读（打印拟生成配置），加 `--write` 才写回，且仅更新 `interfaces` 段。
- `scripts/` 下 shell 脚本自动设置 `PYTHONPATH=<repo>/src/python` 后调用 CLI/TUI。
- `rule-apply.sh <规则名> [--interface 网卡]`、`rule-clear.sh <规则名>` 为规则操作的便捷包装。

## 7. TUI 设计（curses，零依赖）

纯键盘单屏界面（无鼠标、无第三方库）：

- **标题栏**：显示当前主机名（**蓝色**）：`netswitch · <主机名> · 网卡切换控制台`。
- **启动即拉取 + 自动刷新**：进入即拉取实时网卡信息（数量 1~N、有线/无线、连接状态、IP、metric、默认路由）；界面每 ~1.5s 自动刷新，`f` 或 `F5` 手动刷新；底部状态栏显示“最后刷新 yyyyMMdd HH:mm:ss +ZZZZ”。因此开启网卡后 DHCP 获取 IP、链路变化会自动显示。
- **导航**：`↑`/`↓` 在所有条目（网卡在前、规则在后）间移动选中项；数字 `1`…`N` 直接选网卡；`g` 切换规则；`Enter` 对**网卡**执行默认动作（**设为主网卡**）；选中规则时 `Enter` 仅提示，不直接执行。
- **网卡区颜色语义**：
  - **分流生效网卡**（某条已应用规则的出口）→ **黄色**
  - **主网卡**（有默认路由且 metric 最小）→ **绿色**
  - **其他可用网卡** → **蓝色**
  - **被禁止 / 不可用**（管理关闭 admin-down，或未插网线）→ **红色**
  - 数字 `1`…`N` 选中后：`o` 关闭/开启（按**管理状态**判断）、`m` 设为主网卡、`t` 改 metric。
  - 无线网卡额外显示当前 **SSID**（未连接显示 `-`；由 `iw dev <if> link` 获取，回退 `iwgetid -r`）。
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
# PYTHONPATH 指向 src/python（按实际安装路径修改）
Environment=PYTHONPATH=/opt/netswitch/src/python
ExecStart=/usr/bin/python3 -m netswitch.cli apply --yes

[Install]
WantedBy=multi-user.target
```

`install-systemd` 将服务写入 `/etc/systemd/system/` 并 `systemctl enable`。开机联网后自动应用配置（metric + 全部分流规则）。

## 9. 依赖

| 依赖 | 用途 | 是否必需 |
|------|------|----------|
| `ip`（iproute2） | 路由/网卡操作 | 必需 |
| 内核策略路由（`CONFIG_IP_MULTIPLE_TABLES`） | 自定义路由表 + `ip rule`（分流必需） | 必需（分流功能） |
| `nft`（nftables） | 后端 A | 可选，缺省回退 iprule |
| Python 3.9+（标准库） | 运行（json / curses / ipaddress） | 必需 |

> 无第三方 Python 运行时依赖（不依赖 PyYAML、textual）。
> 若内核未启用 `CONFIG_IP_MULTIPLE_TABLES`（或在受限容器/gVisor 中运行），`ip route ... table N` / `ip rule` 会报 `RTNETLINK answers: Operation not supported`，此时**无法分流**（可在 `preflight.sh` 的「策略路由能力」项确认）。

## 10. 错误处理

- 子命令返回非 0：收集 stderr，抛出带命令上下文的 `ExecError`。
- 拉取 CIDR 源失败：告警并回退缓存/`extra`。
- 配置缺失/非法：启动时校验并给出字段级错误提示。
- 部分命令失败：apply 继续执行其余项，最后汇总报告；revert 可恢复。

## 11. 测试策略

详见 [test-plan.md](test-plan.md)。

## 12. 日志

- 目录：`logs/`（仓库根；可用环境变量 `NETSWITCH_LOG_DIR` 覆盖）。
- 文件：`logs/netswitch.log`，按大小轮转（2MB × 5 个）。
- 级别：默认 INFO（记录写类命令与高层动作）；`NETSWITCH_LOG_LEVEL=DEBUG` 可包含只读查询。
- 内容：`exec.run` 记录每条系统命令（`OK`/`FAIL`/`DRY-RUN`）；apply/revert、网卡开关、metric、策略路由、配置写回、CIDR 拉取、CLI/TUI 启动均有记录。
- 日志不可写时静默降级，不影响主流程。
