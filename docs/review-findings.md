# 自查记录（Review Findings）

> 用途：记录设计/实现过程中的自查发现、风险、决策与改进项，随开发持续更新。

## 初次设计自查（M0 阶段）

### 1. [风险] GitHub CIDR 数量导致 ip rule 膨胀
- **描述**：GitHub 官方 meta 接口返回 100+ 条 IPv4 CIDR，若全部用 `ip rule to <cidr>` 会产生大量规则。
- **决策**：首选 nftables 后端（集合 + 1 条 fwmark 规则），纯 iprule 作为无 `nft` 时的回退。
- **状态**：已在 technical.md 4.3 落地。

### 2. [风险] 容器流量能否真正走指定网卡（硬性要求）
- **描述**：分流必须同时覆盖主机与容器（网络命名空间）对外流量；容器经主机 NAT 出网，需确认策略路由作用于转发流量且 MASQUERADE 用出口网卡 IP。
- **决策**：PREROUTING 打标（覆盖转发流量）+ OUTPUT 打标（覆盖本机）；`ip rule fwmark` / `to <cidr>` 同时作用于两类流量。另需关注 rp_filter 宽松模式与 `-o <网卡> MASQUERADE` 显式规则的兼容性。
- **状态**：设计已强化（technical.md 4.4）；集成测试 I-05~I-07 覆盖转发与 NAT 源地址。

### 3. [风险] metric 变更会被 DHCP 续约重置
- **描述**：默认路由由 DHCP 生成，运行时改 metric 后，续约可能恢复原值。
- **决策**：v1 采用“运行时生效 + systemd 开机重放”兜底；后续版本评估 NetworkManager dispatcher 做持久化。
- **状态**：已记录，属已知局限（requirements NFR 未要求持久化 metric）。

### 4. [风险] 关闭网卡后开启依赖 DHCP 自动恢复
- **描述**：`ip link set up` 只恢复链路，IP 与路由需 DHCP 客户端重新获取，有秒级延迟。
- **决策**：符合“临时关闭/开启”语义，文档明确说明；不做静态 IP 写回。
- **状态**：已记录。

### 5. [风险] GitHub 网段更新导致分流漏网
- **描述**：GitHub 新增 IP 段后，未刷新缓存的流量会走默认路由。
- **决策**：TTL 缓存 + 手动 `extra` 补充；`apply` 时提示缓存年龄。
- **状态**：v1 可接受；后续可加定时刷新（systemd timer）。

### 6. [决策] 只重建默认路由，不碰 on-link 路由
- **描述**：metric 优先级由默认路由决定；on-link 路由 metric 仅影响直连子网选路，改动收益低且易出错。
- **决策**：仅重建默认路由。
- **状态**：已落地 technical.md 4.2。

### 7. [决策] 状态记录采用 state.json 而非运行时内存
- **描述**：revert 需跨进程/跨会话恢复原始状态。
- **决策**：apply 前把原始默认路由写入 `data/config/state.json`，revert 读取恢复。
- **状态**：已落地 technical.md 3.3。

### 8. [待办] IPv6 分流
- **描述**：当前 v1 仅 IPv4；GitHub 部分服务存在 IPv6。
- **决策**：v1 明确排除 IPv6（requirements 约束 4），后续版本补 `nft set github6` + ip6 规则。
- **状态**：列入后续。

### 9. [待办] 非 root 时的能力降级
- **描述**：所有写操作需 root；`status` 查询理论上可非 root。
- **决策**：`status` 允许非 root，写操作拒绝；在 cli.py 按子命令区分。
- **状态**：待实现时验证。

---

## 设计复盘（第 2 轮，总体设计 / 可配置化）

从总体设计与可配置化角度逐轮复核，发现并修复以下问题：

1. **[冗余] 配置项过多**：`apply.manage_*`、`systemd` 段、`match.ip_version`、`interfaces[].type`（仅展示）、`cache_file`/`nft_set`/`rule_pref` 显式项。→ 收敛为声明式：metric 出现即调整、规则 enabled 即应用；后端/表名全局化；派生项（缓存文件/nft set/rule 优先级）内部自动。
2. **[硬编码残留] requirements FR6/FR7 仍写“GitHub 分流”**：与 FR4「不硬编码」矛盾。→ 改为“分流规则”。
3. **[设计] 后端应按全局而非每规则**：nft 表与链是共享资源，每规则 backend 易冲突。→ 提升为 `routing.backend` 全局。
4. **[缺陷] 默认值冲突**：fwmark 默认恒为 1 会跨规则冲突。→ 改为按规则序号自动分配（fwmark=1,2,…；table_id=200,201,…；rule_pref=20000+序号）。
5. **[歧义] `metric primary` 语义模糊**：“设为最小值”不明确。→ 用 `metrics.preferred/fallback`（默认 100/600）精确定义。
6. **[缺陷] nft 链重复创建**：命令示例会重复 `add chain`。→ 改为整表 `nft -f` 原子替换，天然幂等；链只建一次，每规则一条 set+两条 rule。
7. **[冗余] 文档重复**：technical §11 与 test-plan 重复；test-plan §6 与 requirements §6 重复。→ technical 指向 test-plan；test-plan 引用 requirements。
8. **[不一致] 目录树**：technical 列出根 README.md，实际曾为 docs/readme.md。→ 当时已修正；后续 README 又移回仓库根（见 changelog）。
9. **[过时] plan.md**：M1 仍写 github.py、M3 仍写“GitHub 分流区”。→ 更新为 cidrs.py / 分流规则区。
10. **[硬编码] preflight.sh 回退 URL 写死 github**：→ 无配置 url 时跳过可达性检查，不硬编码。

---

## 实现阶段自查（M1~M4）

1. **[缺陷] extra 未过滤 IPv6**：`source: manual` 时 `extra` 中的 IPv6/非法项会直接进入结果，违反 v1 仅 IPv4。→ 已修复：统一 `_finish` 过滤。
2. **[缺陷] 无 nft 时 clear_rules 崩溃**：`subprocess` 找不到 `nft` 二进制抛 FileNotFoundError，`check=False` 无法捕获。→ 已修复：`shutil.which("nft")` 守卫。
3. **[缺陷] 空 CIDR 规则仍建路由表**：CIDR 为空的规则会残留路由表与 fwmark 规则。→ 已修复：先取 CIDR，空则跳过。
4. **[缺陷] 测试硬编码版本号导致发布失败**：`test_version_value` 断言 `__version__ == "0.1-dev"`；发布时版本变为 `0.1`，pre-push 钩子跑测试失败、发布中断。→ 已修复：改为校验版本格式（`X.Y` / `X.Y-dev`）。

---

## 第 3 轮自查（安全 / 一致性加固，全部已修复）

本轮以“真实执行行为 vs 文档承诺”为线索逐项验证（含 netns 实测），发现并修复以下问题：

### P0 正确性 / 安全
1. **[缺陷·已验证] `clear_rules` 会误删系统与他人的主表路由**：清理时无条件执行 `ip route del <cidr> table main`，既不判断后端、也不按 `proto 200` 过滤。netns 实测：内核直连路由（`proto kernel`）与他人静态路由（`proto static`）都会被静默删除 → 每次 apply 都可能造成断网。
   **修复**：清理只删“主表中确实存在、`proto = MAINROUTE_PROTO(200)`、且出口网卡匹配”的条目（`ip route del <cidr> proto 200 dev <iface> table main`），并加 netns 集成用例 I-09。
2. **[缺陷·已验证] systemd 单元生成即坏**：模板写成 `apply --yes --config <repo>/data/config/config.yaml` —— 扩展名错（实际是 JSON）+ 顶层 `--config` 放在子命令之后。实测 argparse 报 `unrecognized arguments ... (exit=2)`，即“装得上、开机不生效”；且无 `WorkingDirectory`，而 state/cache 是相对路径。
   **修复**：`--config .../config.json` 前置 + `WorkingDirectory` + 安装前用同一 argparse 校验 `ExecStart`；新增回归测试 T-cli-09/10。
3. **[缺陷·已验证] 配置值零校验**：`table_id` 无范围校验（`254` 会让 `ip route flush table 254` 清空主表）；`nft_table` 未过滤就拼进 `nft -f` 脚本（可注入）；`a-b` 与 `a.b` 归一化后同名 `a_b_v4`（nft 整表失败）；`cache_file`/`state_file` 可指向 `/etc/...`（root 任意写文件，配合“仓库属主非 root + sudo 运行”构成提权面）。
   **修复**：`config._validate()` 白名单校验 + `_data_path()` 数据目录约束（§4.7）。

### P1 健壮性 / 数据一致性
4. **[缺陷] 重复 apply 污染 revert 基线**：每次 apply 都覆写 `original_defaults`，第二次抓到的是“已被自己改过的 metric” → revert 恢复不回去。**修复**：只在首次记录；revert 成功后删除 state。
5. **[缺陷] 配置变更后清理不彻底**：清理目标由当前配置推导，规则被删/改名/换表号后旧的 `ip rule` 与派生路由表永久残留。**修复**：按 `pref ∈ [20000,32000)` + 派生表号区间扫描，并接受 state 里的历史表号。
6. **[缺陷] “先清后建、无回滚”**：预检缺失，任何一条命令失败都会留下半成品。**修复**：`preflight()` → `clear` → 重建，失败 best-effort 回滚；CIDR 也在入口统一规范化，非法项不再进命令。
7. **[缺陷] 同一 CIDR 源一次 apply 最多请求 4 次**（`_build_nft_script` 内部重复拉取 + 主流程 + 清理），且 set 与实际规则可能来自不同批次数据。**修复**：一次拉取、传 map。
8. **[缺陷] 能力探测会写系统且并发误判**：root 下临时增删测试路由（dry-run 也会执行），并发时第二个进程拿到 `File exists` 被误判为“不支持策略路由”；非 root 直接返回 True（谎报支持）。**修复**：改为只读 `ip -j rule show` 探测，非 root 返回“未知”（`None`），TUI/status 明示。
9. **[缺陷·已验证] `set_primary` 对“不在 config.interfaces 的网卡”静默失效**：实测 `set_primary('wlp129s0')` 只把 `enp2s0` 压成 fallback，目标网卡毫无变化 → 结果没有任何 preferred 网卡。**修复**：按探测结果补齐目标网卡。

### P2 体验 / 可观测性 / 工程化
10. **[缺陷] `metric set <iface>` 缺数值**：实测 `int(None)` 抛 `TypeError`，而顶层只捕获 4 类异常 → 用户看到 traceback。**修复**：参数校验（退出码 2）+ 顶层兜底异常（人话 + 日志 traceback）。
11. **[缺陷] TUI 长错误看不见 + 每 1.5s 重复探测**：状态栏单行截断（EOPNOTSUPP 多行提示不可读）；每轮刷新重复跑 `ip` 命令、`mainroute` 下每条规则各 dump 一次主表。**修复**：错误弹层（含 CJK 折行）+ 探测/主表快照复用 + “执行中…”提示。
12. **[缺陷] root 跑过之后普通用户日志静默丢失**：`logs/` 归 root，`setup()` 捕获 `OSError` 后降级 `NullHandler`。**修复**：自动回退到 `$XDG_STATE_HOME`/临时目录并提示一次；`status` 打印日志路径；启动行加发起用户。
13. **[缺陷] 物理网卡识别偏乐观**：VLAN 子接口/`dummy`/`wg`/`ppp` 等可能被当成物理网卡并写默认路由。**修复**：sysfs `device` 优先 + VLAN/虚拟前缀排除；同一网卡多条默认路由取 metric 最小者。
14. **[缺陷] 工程化门禁有漏**：CI 只跑 pytest + check-docs（漏 `check-version.sh`，单一 Python 版本）；`check-docs.sh` 只校验“文件存在”，AGENTS.md 的“改代码必须同步 changelog/folder”全靠人。**修复**：CI 改调 `scripts/check.sh` + 3.9/3.11/3.13 矩阵 + `bash -n`/`compileall`/pyproject 解析；`check-docs.sh` 增加 folder.md 覆盖与 changelog 门禁；新增 `pyproject.toml`。
15. **[缺陷] 文档漂移**：`plan.md` 写“22 个单测”（实际已 160）与不存在的 `Route`/`NetState`；`model.py` 注释漏 `mainroute`。**修复**：全部同步（含本文件与 test-plan/user-manuals/faq）。

### 本轮新增的自动化回归

| 用例 | 覆盖 |
|------|------|
| I-09（netns） | 清理只删 `proto 200`；`proto kernel`/`proto static` 原样保留 |
| I-10（netns） | mainroute apply→clear 闭环，清理后直连子网路由保留 |
| I-11（netns） | 能力探测与 dry-run 清理不改动系统 |
| T-config-10~17 | backend/table_id/fwmark/名字/nft_table/路径校验与探测快照复用 |
| T-route-08~14 | 清理边界、预检零改动、失败回滚、历史残留清理、一次拉取 |
| T-apply-05~07 | 基线不被覆盖、revert 恢复并清理、state 损坏容错 |
| T-cli-07~10 | `metric set` 缺值、顶层兜底、`--config` 位置、systemd 模板解析 |
| T-log-01 | 日志目录不可写时回退 |

---

## 需求变更：IPv6 双栈分流（FR10）—— 决策记录

1. **[决策] 默认仅 IPv4，v6 必须显式开启**：`routing.ip_versions` 默认 `["v4"]`。分流的默认行为不因升级而改变；也避免在无可用 v6 出口的机器上因 v6 网段而意外改变路选。
2. **[决策] 地址族同时支持全局与规则级**：全局统一开双栈，必要时单条规则覆盖（例如只有 github 需要 v6）。
3. **[决策] v6 不可用 = 告警+跳过，而非失败**：内核未启用 IPv6 / 无 v6 网关 / 缺 `CONFIG_IPV6_MULTIPLE_TABLES` 都属于环境差异，不应阻断 v4 分流（对应 preflight 只把它们归入告警）。例外：规则**只**声明 v6 时，该规则整体跳过并告警（不能静默什么都不做）。
4. **[决策] 继续用 `proto 200` 作签名，不分族另起标记**：netns 实测 `ip -6 route add ... proto 200` 可用，且 `ip -6 -j route show` 同样返回 `"protocol": "200"`；清理时额外用“地址族 + 出口网卡”限定，安全性等价。
5. **[决策] v6 网关允许链路本地地址**：v6 默认路由的网关常见为 `fe80::1`，因此 `gateway6` 不做全局地址限定（只需地址合法）；配置支持 `fe80::1%eth0` 写法。
6. **[决策] 只有链路本地地址时不参与分流**：链路本地地址不能作为分流源地址，`detect` 只取全局作用域地址（`scope != link`）；否则宁可不给 `src`。
7. **[已知局限] 域名/通配符仍不在 v6 范围内**（本程序分流只按网段）；域名导出为 CIDR 的能力见后续需求（FR11，同时覆盖 v4/v6）。

---

## 更新记录

| 日期 | 阶段 | 变更 |
|------|------|------|
| - | M0 | 初次设计自查（9 项） |
| - | M0 | 设计复盘第 2 轮（10 项，总体设计/可配置化） |
| - | M4 | 实现阶段自查（3 项） |
| - | M4 | 发布过程缺陷（1 项） |
| - | M6 | 第 3 轮自查（15 项：安全/一致性加固，全部修复并补自动化回归） |
| - | M7 | 需求变更：IPv6 双栈分流（FR10）及其决策记录 |
