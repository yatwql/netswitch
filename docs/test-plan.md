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
| 单元测试 | 纯逻辑：配置解析、默认值填充、`ip -j` 解析、CIDR 合并 | 任意环境 |
| 命令构造测试 | 断言 dry-run 生成的命令序列正确（不真正执行系统命令） | 任意环境 |
| 集成测试 | 在 `ip netns` 隔离环境内真实执行 `ip`/`nft`，验证路由/nft 行为 | 需 root + netns |
| 手动验收 | 真实多网卡（1~N）+ 容器出网验证 | 目标机 |

原则：**不碰真实网卡**的自动化测试优先；真实网卡操作一律走 dry-run 或 netns。

## 3. 测试环境

- 单元/命令构造：普通用户即可，无需 root。
- 集成：root 权限 + `iproute2` + `nftables`；用 `ip netns add ns1` 建隔离网络命名空间，`ip -n ns1 ...` 操作。
- 依赖注入：核心模块接受 `runner`（命令执行器）参数，测试注入 fake runner 记录/回放命令，实现 dry-run 断言与单元隔离。

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

### 4.3 ip.py — ip 输出解析与命令构造

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-ip-01 | P0 | 解析 `ip -j route` 默认路由 | 提取 gateway/dev/metric/src |
| T-ip-02 | P0 | 解析 `ip -j link` 状态 | 提取 UP/DOWN、MAC、类型 |
| T-ip-03 | P1 | 构造 `ip route del/add` 命令 | 参数正确 |
| T-ip-04 | P1 | 构造 `ip rule add fwmark`/`to <cidr>` | 参数正确 |

### 4.4 iface.py — 网卡开关 / metric（FR1/FR2）

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-iface-01 | P0 | `iface down` 普通网卡 | 生成 `ip link set <if> down` |
| T-iface-02 | P0 | `iface down` 最后一张已连接网卡 | 拒绝，需 `--force` |
| T-iface-03 | P0 | `iface up` | 生成 `ip link set <if> up` |
| T-iface-04 | P0 | `metric set` | 先删后加默认路由，metric 正确 |
| T-iface-05 | P0 | `metric primary` | 目标网卡 metric 最小，其余递增 |
| T-iface-06 | P0 | 记录改动前状态 | state.json 含原始 gateway/metric |

### 4.5 cidrs.py — CIDR 来源（FR4）

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-cidrs-01 | P0 | `source: url` 拉取成功 | 按 `fields` 提取 CIDR |
| T-cidrs-02 | P0 | 拉取失败 | 回退缓存；无缓存则报错并允许 `extra` |
| T-cidrs-03 | P0 | 缓存未过期 | 不重复请求 |
| T-cidrs-04 | P0 | `extra` 合并 | 与来源 CIDR 合并去重 |
| T-cidrs-05 | P0 | `source: manual` | 仅用 `extra` |

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

### 4.7 apply.py — 编排 / revert（FR7）

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-apply-01 | P0 | `apply` 全量 | 依次执行 metric + rules（不改变网卡开关状态） |
| T-apply-02 | P0 | `revert` | 恢复原始默认路由 + 清理规则 |
| T-apply-03 | P0 | 部分命令失败 | 继续执行其余，最后汇总报告 |
| T-apply-04 | P0 | `--dry-run` | 不改变系统状态 |

### 4.8 cli.py — 命令行（FR5）

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-cli-01 | P0 | 各子命令参数解析 | 正确分派 |
| T-cli-02 | P0 | `rule apply <规则名>` | 规则名来自配置，非硬编码 |
| T-cli-03 | P0 | 未知规则名 | 报错提示可用规则 |
| T-cli-04 | P1 | 退出码 | 成功 0，失败非 0 |
| T-cli-05 | P0 | 非交互无 `--yes` 的破坏性操作 | 拒绝执行 |
| T-cli-06 | P0 | `-y/--yes` | 跳过确认 |

### 4.9 tui.py — TUI（FR5）

| 编号 | 优先级 | 用例 | 预期 |
|------|--------|------|------|
| T-tui-01 | P1 | 启动渲染（curses） | 主界面含网卡区 + 规则区 |
| T-tui-02 | P1 | 与 CLI 共用核心 | 调用的核心函数与 cli 一致（功能一致保障） |
| T-tui-03 | P1 | 「重新探测网络」按钮 | 触发 detect，弹层展示结果并提供写入/取消 |
| T-tui-04 | P1 | 快捷键触发 | 按键与按钮点击调同一处理函数，字母正确显示在按钮上 |
| T-tui-05 | P0 | 启动拉取实时信息 | 进入即拉取网卡数量/类型/连接状态；有线/无线高亮，未连接显示“未有连接” |

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

## 5. 集成测试（netns 隔离）

在 netns 内搭建虚拟多网卡（1~N）+ 网关，验证真实系统行为（不碰物理网卡）：

| 编号 | 优先级 | 场景 | 验证点 |
|------|--------|------|--------|
| I-01 | P0 | 双 veth 默认路由 + metric | `metric set` 后默认路由 metric 正确 |
| I-02 | P0 | `iface down/up` | down 后该路由消失，up 后恢复 |
| I-03 | P0 | 策略路由 nftables 后端 | 指定网段走指定网卡（`ip route get <ip>` 断言） |
| I-04 | P0 | 策略路由 iprule 后端 | 同上 |
| I-05 | P0 | 容器转发：规则目标 | netns 间转发按策略走指定出口（`ip route get <ip> from <容器IP> iif <网桥>` 断言） |
| I-06 | P0 | 容器转发：NAT 源地址 | 转发后源地址改写为指定出口网卡 IP（netns + masquerade 验证） |
| I-07 | P1 | rp_filter 宽松模式 | 关闭/宽松 rp_filter 时转发不被丢弃 |
| I-08 | P0 | `revert` | `ip route`/`ip rule`/nft 恢复原状 |

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

- 每次改动运行：`pytest src/test/`。
- 命令构造测试与单元测试无 root 可跑，纳入 CI。
- netns 集成测试需 root，作为本地/CI 特权步骤。
- 发布 `0.1.0` 前（由 `dev` 合并到 `master`）全量通过 + 手动验收完成。

## 9. 风险与未覆盖项

- 真实 DHCP 续约重置 metric：手动验证 + systemd 兜底，不自动化。
- 真实容器（Docker/LXC）出网 NAT：手动验证，netns 仅做近似模拟。
- GitHub 网段变更：缓存 TTL + `extra` 补充，测试覆盖缓存回退逻辑。
- IPv6：v1 不覆盖，列入后续。

## 10. 执行方式

```bash
# 单测 + 命令构造（无需 root；pytest.ini 已配置 testpaths=src/test）
scripts/run-test.sh              # 或 python3 -m pytest -q / make test

# 完整核对（测试 + 文档一致性，提交门槛）
scripts/check.sh                # 或 make check

# 全部（含 netns 集成，需 root；集成测试暂未加入，后续用 -m integration）
scripts/run-test.sh
```
