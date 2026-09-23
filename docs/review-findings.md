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

## 更新记录

| 日期 | 阶段 | 变更 |
|------|------|------|
| - | M0 | 初次设计自查（9 项） |
| - | M0 | 设计复盘第 2 轮（10 项，总体设计/可配置化） |
| - | M4 | 实现阶段自查（3 项） |
| - | M4 | 发布过程缺陷（1 项） |
