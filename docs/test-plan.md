# 测试计划（Test Plan）

> 项目：netswitch
> 对应实现：`src/python/netswitch/`，测试代码：`src/test/`
> 说明：本文档定义测试目标、层次、用例清单、环境与执行方式，与 `requirements.md`（需求/验收）、`technical.md`（设计）保持一致。

## 1. 测试目标与范围

- 目标：确保脚本接口与 TUI 接口**功能一致**、配置通用化（不硬编码 `github`）、对系统路由/nft 的变更**可逆且幂等**、危险操作有防护。
- 范围：覆盖 FR1–FR9 全部功能；不含真实物理网卡破坏性测试（用 netns 隔离替代）。
- 边界：真实 DHCP/NetworkManager 行为、真实容器出网链路，列入「手动验收」而非自动化。

## 2. 测试策略（测试金字塔）

| 层次 | 说明 | 运行位置 |
|------|------|----------|
| 单元测试 | 纯逻辑：配置解析/校验、默认值填充、`ip -j` 解析、CIDR 规范化与合并、状态文件语义 | 任意环境 |
| 命令构造测试 | 断言 dry-run 生成的命令序列正确（不真正执行系统命令） | 任意环境 |
| 集成测试（netns） | 用 `unshare -rn` 起临时 netns（**不需 root**）跑真实 `ip` 命令，验证“只删自己的路由”、apply/clear 闭环、dry-run 不改系统 | 需 Linux + 非特权 user namespace；不满足自动 skip |
| 手动验收 | 真实多网卡（1~N）+ 容器出网验证 | 目标机 |

原则：**不碰真实网卡**的自动化测试优先；真实网卡操作一律走 dry-run 或 netns。

## 3. 测试环境

- 单元/命令构造：普通用户即可，无需 root。
- 集成：`unshare -rn`（非特权 user namespace）+ `iproute2`；用 `pytest.mark.integration` 标记，环境不支持时自动 skip。
- 依赖注入：monkeypatch 替换 `iproute2` 查询与命令执行器，断言命令序列、校验副作用边界。
- 当前规模：**218 个用例通过**（含 5 个 netns 集成用例），全部无需 root。

## 4. 测试用例清单

> 优先级：P0 = 必须，P1 = 应该，P2 = 可选。测试文件位于 `src/test/`。

### 4.1 exec.py — 命令执行封装

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-exec-01 | P0 | 非 root 调用写操作 | 抛 `PermissionError`，提示明确 |
| T-exec-02 | P0 | `dry_run=True` 时执行 | 仅打印命令，不真正执行 |
| T-exec-03 | P0 | 子命令返回非 0 | 抛 `ExecError`，含命令与 stderr |
| T-exec-04 | P1 | 日志记录 | 每条命令与结果可追踪 |

### 4.2 config.py — 配置加载/校验/探测

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-config-01 | P0 | 加载合法 JSON | 得到正确 dataclass |
| T-config-02 | P0 | 缺省字段 | 应用 3.2 节默认值 |
| T-config-03 | P0 | `interfaces` 为空 | 自动探测物理网卡（排除 veth/br-/lzc-/docker 等） |
| T-config-04 | P0 | `gateway`/`metric` 缺省 | 从当前路由自动探测填充 |
| T-config-05 | P0 | 规则无 `name` 或重复 `name` | 报字段级错误 |
| T-config-06 | P0 | `table_id`/`fwmark` 跨规则冲突 | 报错 |
| T-config-07 | P1 | 非法字段值（负 metric、越界 table_id） | 报错 |
| T-config-08 | P0 | 配置中不出现 `github` 字样也能工作 | 验证通用性（规则名任意） |
| T-config-09 | P1 | `metrics.preferred/fallback` 缺省 | 默认 100/600 |
| T-config-10 | P0 | `routing.backend` 拼错 | 报错（不再静默回退 auto） |
| T-config-11 | P0 | `table_id` ∈ {0,1,199,253,254,255,999} | 报错（含内核保留表） |
| T-config-12 | P0 | `fwmark` = 0 / 负数 / >0xFFFFFFFF | 报错 |
| T-config-13 | P0 | `nft_table` / 规则名含非法字符或过长 | 报错（防注入） |
| T-config-14 | P0 | 规则名归一化后重名（`a-b` / `a.b`） | 报错（否则 nft 整表失败） |
| T-config-15 | P0 | `state_file`/`cache_file` 越界（`/etc/...`、`../`） | 报错（数据目录约束） |
| T-config-16 | P1 | 相对路径解析 | 按仓库根解析为绝对路径，不随 CWD 漂移 |
| T-config-17 | P1 | 传入 `probe_map` 快照 | 不再执行 `ip` 探测命令 |
| T-config-18 | P0 | `routing.ip_versions` 缺省/双栈/规则级覆盖 | 默认 `["v4"]`；规则级优先 |
| T-config-19 | P0 | `ip_versions` 非法（`v5`/空/重复/字符串） | 报错 |
| T-config-20 | P0 | `gateway6` 校验 | 必须是 IPv6（允许 `fe80::1%eth0`）；写成 v4 地址报错；`gateway` 写成 v6 也报错 |

### 4.3 ip.py — ip 输出解析与命令构造

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-ip-01 | P0 | 解析 `ip -j route` 默认路由 | 提取 gateway/dev/metric/src |
| T-ip-02 | P0 | 解析 `ip -j link` 状态 | 提取 UP/DOWN、MAC、类型 |
| T-ip-03 | P1 | 构造 `ip route del/add` 命令 | 参数正确 |
| T-ip-04 | P1 | 构造 `ip rule add fwmark`/`to <cidr>` | 参数正确 |
| T-ip-05 | P0 | 策略路由能力探测（root，不支持） | **只读** `ip rule show` 判定为 False，不增删路由 |
| T-ip-06 | P0 | 能力探测（非 root） | 返回“未知”（None），不谎报支持 |
| T-ip-07 | P0 | `rule_pref` 超出上限 | 报错（不撞内核默认 `pref 32766`） |
| T-ip-08 | P0 | 物理网卡识别 | VLAN 子接口/`dummy`/`wg`/`ppp`/`macvlan` 不算物理；sysfs `device` 节点优先 |
| T-ip-09 | P0 | 地址族参数与命令 | `family_args(4)==[]`（不改变既有 v4 命令）、`(6)==["-6"]`；`route_list(6)`/`rule_list(6)` 生成 `ip -6 -j ...` |
| T-ip-10 | P0 | v6 可用性/能力探测 | `family_available(6)` 只读探测；不支持时 `policy_routing_supported(6)` 返回 False；非 root 返回 None |
| T-ip-11 | P1 | nft set 名按族 | `github_v4` / `github_v6` |

### 4.4 iface.py — 网卡开关 / metric（FR1/FR2）

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-iface-01 | P0 | `iface down` 普通网卡 | 生成 `ip link set <if> down` |
| T-iface-02 | P0 | `iface down` 最后一张已连接网卡 | 拒绝，需 `--force` |
| T-iface-03 | P0 | `iface up` | 生成 `ip link set <if> up` |
| T-iface-04 | P0 | `metric set` | 先删**该网卡全部**默认路由，再添加，metric 正确 |
| T-iface-05 | P0 | `metric primary` | 目标网卡 metric 最小，其余递增 |
| T-iface-06 | P0 | 记录改动前状态 | state.json 含原始 gateway/metric |
| T-iface-07 | P0 | `metric primary` 目标网卡不在配置 `interfaces` 中 | 自动补齐并真的设为 preferred（不静默失效） |
| T-iface-08 | P1 | `metric=None`（revert 恢复） | 重建不带 metric 的默认路由 |

### 4.5 cidrs.py — CIDR 来源（FR4）

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-cidrs-01 | P0 | `source: url` 拉取成功 | 按 `fields` 提取 CIDR |
| T-cidrs-02 | P0 | 拉取失败 | 回退缓存；无缓存则报错并允许 `extra` |
| T-cidrs-03 | P0 | 缓存未过期 | 不重复请求 |
| T-cidrs-04 | P0 | `extra` 合并 | 与来源 CIDR 合并去重 |
| T-cidrs-05 | P0 | `source: manual` | 仅用 `extra` |
| T-cidrs-06 | P0 | 非法 CIDR（域名、`1.2.3.4/999`） | 丢弃并告警；网络地址归一化（`1.2.3.4/24`→`1.2.3.0/24`） |
| T-cidrs-07 | P0 | 响应非 JSON 对象 / 超过 8MB / `fields` 为空 | 报错并走缓存回退 |
| T-cidrs-08 | P0 | IPv6 CIDR | 保留并规范化（`2001:db8:99::5/48`→`2001:db8:99::/48`）；`split_families` 拆分正确 |

### 4.6 routing.py — 分流规则（FR3）

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-route-01 | P0 | 规则应用（nftables 后端） | 建路由表 + nft set/chain/rule + `ip rule fwmark` |
| T-route-02 | P0 | 规则应用（iprule 后端） | 建路由表 + 逐条 `ip rule to <cidr>` |
| T-route-03 | P0 | 全局 `routing.backend=auto` 且无 `nft` | 自动回退 iprule |
| T-route-04 | P0 | 规则清理（单规则） | 删除 set/规则/路由表/ip rule |
| T-route-05 | P0 | 多规则并存 | 各自 table_id/fwmark/set 互不冲突 |
| T-route-06 | P1 | 出口网卡为 DOWN | 告警并阻止（可 `--force`） |
| T-route-07 | P0 | 幂等：重复 apply | 结果一致，无残留重复规则 |
| T-route-08 | P0 | 清理只删自己的产物 | 主表无 `proto 200` 条目时不执行任何删除；`proto static`/`kernel` 路由不动 |
| T-route-09 | P0 | 清理不碰“其它出口”的网段；待重建条目保留 | 不产生瞬时断流 |
| T-route-10 | P0 | 预检失败（网卡不存在/网关不可解/CIDR 非法/`ip rule` 超上限） | **零改动**抛错，不先清空 |
| T-route-11 | P0 | 重建中途失败 | best-effort 回滚清理后抛错 |
| T-route-12 | P0 | 清理历史残留 `ip rule`/路由表 | 按 `pref` 区间与派生表号清理，不碰 254 等保留表 |
| T-route-13 | P1 | 同一 CIDR 源一次 apply | 只拉取一次（每条规则一次） |
| T-route-14 | P0 | 切换后端后清理 | 即使当前不是 nftables 后端，也会删历史 nft 表 |
| T-route-15 | P1 | 出口网卡未连接/非物理 | 告警并继续应用（不阻断） |
| T-route-16 | P0 | 地址族解析 | 默认 `(4,)`；全局双栈 `(4,6)`；规则级覆盖 `(6,)` |
| T-route-17 | P0 | v6 mainroute | `ip -6 route replace <v6> … proto 200` |
| T-route-18 | P0 | v6 iprule | `ip -6 route add default … table T` + `ip -6 rule add to <v6> …` |
| T-route-19 | P0 | v6 nftables | `github_v6` 集合 + `ip6 daddr` + `ip -6 rule add fwmark` |
| T-route-20 | P0 | v6 降级（不可用/无网关/无 v6 策略路由） | 告警跳过 v6，**v4 仍生效**；只声明 v6 的规则整体跳过 |
| T-route-21 | P0 | v6 清理 | `ip -6 route del … proto 200 …`；v6 不可用时不发任何 `-6` 命令 |
| T-route-22 | P0 | 无 table_id/fwmark 的手工 Config | 预检报清晰错误（不再 `TypeError`） |

### 4.7 apply.py — 编排 / revert（FR7）

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-apply-01 | P0 | `apply` 全量 | 依次执行 metric + rules（不改变网卡开关状态） |
| T-apply-02 | P0 | `revert` | 恢复原始默认路由 + 清理规则 |
| T-apply-03 | P0 | 部分命令失败 | 继续执行其余，最后汇总报告 |
| T-apply-04 | P0 | `--dry-run` | 不改变系统状态 |
| T-apply-05 | P0 | 二次 apply | 不覆盖 `original_defaults`（revert 能恢复真实原值） |
| T-apply-06 | P0 | `revert` | 恢复原始默认路由 + 清理历史表号 + 删除 state 文件 |
| T-apply-07 | P1 | state 文件损坏/非对象 | 按“无记录”处理并告警，不抛异常 |

### 4.8 cli.py — 命令行（FR5）

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-cli-01 | P0 | 各子命令参数解析 | 正确分派 |
| T-cli-02 | P0 | `rule apply <规则名>` | 规则名来自配置，非硬编码 |
| T-cli-03 | P0 | 未知规则名 | 报错提示可用规则 |
| T-cli-04 | P1 | 退出码 | 成功 0，失败非 0 |
| T-cli-05 | P0 | 非交互无 `--yes` 的破坏性操作 | 拒绝执行 |
| T-cli-06 | P0 | `-y/--yes` | 跳过确认 |
| T-cli-07 | P0 | `metric set` 缺数值 | 报错退出码 2（不再 `TypeError` traceback） |
| T-cli-08 | P0 | 未预期异常 | 顶层兜底：人话提示 + 日志 traceback，退出码 1 |
| T-cli-09 | P0 | `--config` 位置 | 必须在子命令前；错误位置报参数错误（systemd 单元回归） |
| T-cli-10 | P0 | systemd 模板 `ExecStart` | 能被同一 argparse 解析；写错顺序时 `install-systemd` 报错 |

### 4.9 tui.py — TUI（FR5）

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-tui-01 | P1 | 启动渲染（curses） | 主界面含网卡区 + 规则区 |
| T-tui-02 | P1 | 与 CLI 共用核心 | 调用的核心函数与 cli 一致（功能一致保障） |
| T-tui-03 | P1 | 「重新探测网络」按钮 | 触发 detect，弹层展示结果并提供写入/取消 |
| T-tui-04 | P1 | 快捷键触发 | 按键与按钮点击调同一处理函数，字母正确显示在按钮上 |
| T-tui-05 | P0 | 启动拉取实时信息 | 进入即拉取网卡数量/类型/连接状态；有线/无线高亮，未连接显示“未有连接” |
| T-tui-06 | P1 | 长错误展示 | 折行后走错误弹层（`wrap_text` 单测覆盖 CJK 宽度） |

### 4.10 detect.py — 网络自动探测（FR9）

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-detect-01 | P0 | 识别物理网卡 | 排除 veth/br-/lzc-/docker/lo/tun 等 |
| T-detect-02 | P0 | 判断有线/无线 | 按 /sys/class/net/<if>/wireless 区分 |
| T-detect-03 | P0 | 提取 IP/网关/metric | 与 `ip -j` 输出一致 |
| T-detect-04 | P0 | 默认 dry-run | 只打印拟生成 JSON，不写盘 |
| T-detect-05 | P0 | `--write` 合并 | 只更新 interfaces，保留 rules 等字段 |
| T-detect-06 | P1 | 规则引用不存在的网卡 | 告警不自动删 |
| T-detect-07 | P0 | 连接状态判定 | operstate/NO-CARRIER → 已连接/未插网线/未有连接 |
| T-detect-08 | P1 | 网卡数量不固定（1~3） | 按实际探测数量返回 |
| T-detect-09 | P1 | 同一网卡多条默认路由 | 取 metric 最小者 |
| T-detect-10 | P0 | v6 地址/网关探测 | 填 `ip6`/`gateway6`；仅链路本地时 `ip6` 为 None |
| T-detect-11 | P0 | 系统未启用 IPv6 | 不执行任何 `ip -6` 查询，字段留空 |
| T-detect-12 | P1 | `detect --write` 片段 | 含 `gateway6`（探测到时） |

### 4.11 status.py / log.py

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-status-01 | P1 | 单条规则状态查询失败 | 打印失败原因，其余部分照常输出 |
| T-status-02 | P1 | `mainroute` 后端多规则 | 主表只 dump 一次（结果复用） |
| T-status-03 | P1 | 非 root | 提示“策略路由能力未确认”并显示日志路径 |
| T-status-04 | P1 | 双栈展示 | 网卡行含 `IPv6=`/`网关6=`，规则行含 `族=v4+v6`；系统无 v6 时提示“IPv6: 不可用” |
| T-log-01 | P0 | 首选目录不可写 | 自动回退到用户可写目录并在 stderr 提示 |
| T-log-02 | P1 | 启动行 | 含版本、主机名、发起用户 |

## 5. 集成测试（netns 隔离）

已自动化的部分在 `src/test/test_integration_netns.py`：用 `unshare -rn` 在临时 netns 内跑**真实** `ip` 命令（无需宿主 root），环境不满足时自动 skip。

| 编号 | 优先级 | 场景 | 验证点 | 状态 |
|------|--------|------|--------|------|
| I-01 | P0 | 双 veth 默认路由 + metric | `metric set` 后默认路由 metric 正确 | 手动验收 |
| I-02 | P0 | `iface down/up` | down 后该路由消失，up 后恢复 | 手动验收 |
| I-03 | P0 | 策略路由 nftables 后端 | 指定网段走指定网卡（`ip route get <ip>` 断言） | 手动验收 |
| I-04 | P0 | 策略路由 iprule 后端 | 同上 | 手动验收 |
| I-05 | P0 | 容器转发：规则目标 | netns 间转发按策略走指定出口 | 手动验收 |
| I-06 | P0 | 容器转发：NAT 源地址 | 转发后源地址改写为指定出口网卡 IP | 手动验收 |
| I-07 | P1 | rp_filter 宽松模式 | 关闭/宽松 rp_filter 时转发不被丢弃 | 手动验收 |
| I-08 | P0 | `revert` | `ip route`/`ip rule`/nft 恢复原状 | 手动验收 |
| I-09 | P0 | 清理只删 `proto 200` | `proto static` 与 `proto kernel` 路由原样保留；自己的路由被清掉 | **已自动化** |
| I-10 | P0 | mainroute apply→clear 闭环 | veth + 网关下 `ip route replace` 生效（`proto 200`、dev 正确），清理后消失且直连子网路由保留 | **已自动化** |
| I-11 | P0 | dry-run 不改系统 | 能力探测与 `clear_rules(dry_run=True)` 前后 `ip -j route` 完全一致 | **已自动化** |
| I-12 | P0 | v6 清理只删 `proto 200` | v6 主表中他人的 `proto static` 路由保留；自己的 v6 路由被清掉 | **已自动化** |
| I-13 | P0 | v6 mainroute apply→clear 闭环 | `ip -6 route replace … proto 200` 生效（dev 正确），清理后消失且 v6 直连子网路由保留 | **已自动化** |

## 6. 手动验收清单（目标机）

对齐 [requirements.md](requirements.md) 第 6 节「验收标准」，逐项在目标机验证（此处不重复罗列）。

## 7. 测试工具与依赖

| 工具 | 用途 |
|------|------|
| pytest | 测试框架 |
| pytest-mock / unittest.mock | 命令执行 mock |
| 手工验证 | curses TUI 渲染 |
| iproute2 / nftables / netns | 集成测试 |

依赖写入 `src/test/` 或根 `requirements-dev.txt`。

## 8. 回归与 CI

- 每次改动运行：`scripts/run-test.sh`（= `scripts/check.sh` 的一部分）。
- 命令构造/单元/netns 集成测试**均无需 root**，全部纳入 CI（`.github/workflows/ci.yml` 直接调用 `scripts/check.sh`，并在 Python 3.9/3.11/3.13 上跑）。
- CI 额外跑 shell 语法检查（`bash -n`）、`compileall` 与 `pyproject.toml` 解析。
- 发布 `X.Y` 前（由 `dev` 合并到 `master`）全量通过 + 手动验收完成。

## 9. 风险与未覆盖项

- 真实 DHCP 续约重置 metric：手动验证 + systemd 兜底，不自动化。
- 真实容器（Docker/LXC）出网 NAT：手动验证，netns 仅做近似模拟。
- GitHub 网段变更：缓存 TTL + `extra` 补充，测试覆盖缓存回退逻辑。
- IPv6：v1 不覆盖，列入后续。

## 10. 执行方式

```bash
# 单测 + 命令构造 + netns 集成（无需 root；环境不支持时集成用例自动 skip）
scripts/run-test.sh              # 或 python3 -m pytest -q / make test

# 只跑 netns 集成用例
scripts/run-test.sh -m integration

# 完整核对（测试 + 文档一致性 + 版本一致性，提交门槛）
scripts/check.sh                # 或 make check
```
