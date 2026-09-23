# 用户手册（User Manual）

> 适用对象：目标主机的使用者（非开发者）。
> **日常使用只需二选一**：CLI（`scripts/cli-netswitch.sh`）**或** TUI（`scripts/tui-netswitch.sh`）——两者功能一致，不必都跑。
> 用法疑难见 [faq.md](faq.md)；设计细节见 [technical.md](technical.md)。

## 1. 这个软件能做什么

在 Debian 主机（1~N 张物理网卡 + 容器）上：

1. **开关物理网卡**：临时关闭 / 再开启某张网卡。
2. **调整优先级（设主网卡）**：决定默认流量走哪张网卡。
3. **流量分流**：把某类流量（初始为 GitHub，主机与容器对外访问都算）指定走某张网卡；可随时撤销。
4. **全可配置 + 双入口**：配置集中在 `data/config/config.json`，网卡信息运行时自动探测；CLI 与 TUI 功能一致。

> ⚠️ 这些操作多为**高危**（可能影响网络连通）。**操作前先跑预检**；首次执行建议加 `--dry-run` 先看命令。

## 2. 快速上手（约 5 分钟）

### 2.1 环境要求

| 项 | 要求 |
|----|------|
| 系统 | Debian（含 systemd、iproute2） |
| 权限 | root（写操作；`status`/`detect` 只读无需 root） |
| Python | 3.9+（**无第三方依赖**，仅标准库） |
| 可选 | `nftables`（策略路由首选后端；无则自动回退 `iprule`） |

```bash
sudo apt-get install -y iproute2 python3
```

### 2.2 首次安装（一次性）

```bash
sudo scripts/install.sh                  # 检查环境 + 生成配置并自动探测网卡 + 预检
sudo scripts/install.sh --with-systemd   # 追加：开机自恢复
```

安装会依次完成：检测 `python3` → 生成 `data/config/config.json` 并**自动探测本机网卡** → 运行预检。

### 2.3 日常使用（二选一）

**方式 A：CLI**
```bash
scripts/cli-netswitch.sh status            # 查看状态
scripts/cli-netswitch.sh apply --dry-run   # 可选：先看将执行的命令
sudo scripts/cli-netswitch.sh apply        # 按配置应用（metric + 分流规则）
```

**方式 B：TUI**
```bash
scripts/tui-netswitch.sh                   # 进入界面：↑/↓ 导航，按快捷键操作
```

### 2.4 高危操作前先预检

```bash
sudo scripts/preflight.sh                      # 自动探测网卡
sudo scripts/preflight.sh enp2s0 wlp129s0      # 或显式指定网卡
```

- 退出码：`0` 通过 / `1` 有阻断项（勿继续）/ `2` 有告警（注意）。
- 检查项：root、必需命令、nft 后端、网卡状态/网关/metric、逐网卡关闭风险、`ip_forward`/`rp_filter`、CIDR 源可达性。

## 3. 配置（data/config/config.json）

首次用 `install.sh` 自动生成；也可从示例复制后修改：

```bash
cp data/config/config.example.json data/config/config.json
```

> JSON **不支持注释**；字段含义见下方 3.2 与 [technical.md](technical.md) §3.2。
> 默认 `rules` 为**空**（通用）——要分流某类流量，在 `rules` 里添加一条；`github` 仅作示例。

### 3.1 最小可用配置（示例：GitHub 走无线）

```json
{
  "version": 1,
  "interfaces": [
    { "name": "enp2s0" },
    { "name": "wlp129s0" }
  ],
  "rules": [
    {
      "name": "github",
      "interface": "wlp129s0",
      "cidrs": {
        "url": "https://api.github.com/meta",
        "fields": ["git", "web", "api"]
      }
    }
  ]
}
```

### 3.2 完整配置与字段说明

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

| 字段 | 说明 |
|------|------|
| `interfaces[].name` | 物理网卡名；整个 `interfaces` 可省略（缺省自动探测） |
| `interfaces[].gateway` | 网关；缺省自动探测 |
| `interfaces[].metric` | 希望设定的 metric；缺省不调整 |
| `metrics.preferred` / `fallback` | `metric primary` 用：主网卡 / 其余网卡的值（默认 100 / 600） |
| `routing.backend` | 策略路由后端：`auto` / `nftables` / `iprule` |
| `routing.nft_table` | nft 表名（默认 `netswitch`） |
| `rules[].name` | 规则唯一名（任意，如 `github`） |
| `rules[].enabled` | 是否启用（默认 `true`） |
| `rules[].interface` | 生效网卡；空 = 不分流 |
| `rules[].cidrs.source` | `url`（从 URL 拉取）/ `manual`（仅用 extra） |
| `rules[].cidrs.url` / `fields` | 从该 URL 返回 JSON 的哪些字段提取 CIDR |
| `rules[].cidrs.ttl_hours` / `extra` | 缓存时长（默认 24）/ 手动补充网段 |
| `rules[].table_id` / `fwmark` | 缺省自动分配且保证唯一 |
| `state_file` | 原始状态记录位置（`revert` 依赖，默认 `data/config/state.json`） |

### 3.3 新增一条分流规则

在 `rules` 下复制一段，改 `name` / `url` / `fields` / `interface` 即可（`table_id`/`fwmark` 不填会自动避让），**无需改程序**。也可以先只写 `name`，稍后在 TUI 里用 `e` 选择生效网卡。

## 4. 常用命令与按键速查

### 4.1 CLI 子命令

统一入口 `scripts/cli-netswitch.sh <子命令>`（写操作需 root，默认确认，`-y/--yes` 跳过）：

| 子命令 | 作用 |
|--------|------|
| `status` | 查看状态（主机名、版本、程序更新时间、网卡/IP/状态/metric/规则） |
| `detect [--write]` | 重新探测网卡；`--write` 写回 `interfaces` |
| `iface up\|down <name> [--force]` | 开启 / 关闭网卡 |
| `metric set <name> <n>` | 设置 metric（越小越优先） |
| `metric primary <name>` | 设为主网卡 |
| `rule apply <rule> [--interface <iface>]` | 应用某条分流规则（可临时改出口） |
| `rule clear <rule>` | 撤销某条分流规则（**写回配置**） |
| `apply [--force]` | 按配置应用全部（metric + 规则） |
| `revert` | 撤销全部改动（恢复原始默认路由） |
| `install-systemd` | 安装并启用开机自恢复服务 |

### 4.2 TUI 按键

启动：`scripts/tui-netswitch.sh`（标题栏显示当前主机名）

| 按键 | 作用 |
|------|------|
| `↑` / `↓` | 移动选中项（网卡在前、规则在后） |
| `1`…`N` / `g` | 数字选网卡 / `g` 切换规则 |
| `Enter` | 网卡 → **设为主网卡**；规则 → 仅提示 |
| `o` / `m` / `t` | 开关网卡 / 设为主网卡 / 改 metric |
| `e` / `p` / `c` | 规则：选生效网卡 / 应用 / 撤销（`e`/`c` 均写回配置） |
| `d` / `a` / `r` | 重新探测 / 应用全部 / 撤销全部 |
| `f` / `F5` | 刷新 |
| `Esc` | 返回主页面并刷新（输入框中取消当前输入） |
| `q` / `Ctrl+C` | 退出（输入框中 `Ctrl+C` 也可退出） |

- 网卡颜色：**分流生效网卡黄 / 主网卡绿 / 其他网卡蓝 / 被禁止或不可用红**；分流规则生效时，其出口网卡在规则行「生效网卡」与网卡列表均显示**黄色**。**无线网卡额外显示当前 SSID**（未连接显示 `-`）。
- 界面每 ~1.5s 自动刷新（底部显示「最后刷新 `yyyyMMdd HH:mm:ss`」）；标题栏主机名显示为**蓝色**，标题下方显示**版本号与程序更新时间**（`yyyyMMdd HH:mm:ss +ZZZZ`）；所有字母键**不区分大小写**。

### 4.3 便捷包装脚本

```bash
scripts/status.sh
scripts/detect.sh            # 重新探测网络
scripts/iface-down.sh enp2s0
scripts/iface-up.sh   enp2s0
scripts/set-metric.sh enp2s0 200
scripts/rule-apply.sh github
scripts/rule-clear.sh github
scripts/apply.sh
scripts/revert.sh
```

### 4.4 确认与跳过确认

- 破坏性操作（开关网卡、改 metric、规则改写、apply/revert）**默认需确认**（TUI 弹 `y`；CLI 提示 `[y/N]`）。
- CLI 跳过：加 `-y` / `--yes`，或设环境变量 `NETSWITCH_YES=1`；`--dry-run` 因不实际执行而无需确认。
- 非交互（非 TTY）环境若未跳过会**拒绝执行**，避免脚本误改网络。包装脚本会透传参数，如 `NETSWITCH_YES=1 scripts/apply.sh`。

### 4.5 测试与文档核对（开发/运维）

```bash
scripts/run-test.sh      # 跑测试
scripts/check.sh         # 提交门槛：测试 + 文档一致性
make test / make check   # 等价入口
```

## 5. 典型操作场景

### 场景 A：临时关掉有线，只走无线
```bash
sudo scripts/preflight.sh
scripts/cli-netswitch.sh iface down enp2s0     # 无线仍承载默认路由，安全
scripts/cli-netswitch.sh status                # 确认
scripts/cli-netswitch.sh iface up enp2s0       # 恢复
```

### 场景 B：让无线成为默认出口（设为主网卡）
```bash
scripts/cli-netswitch.sh metric primary wlp129s0
# TUI 等价：选中该网卡按 m（或 Enter）→ y
```

### 场景 C：GitHub（含容器）走无线
```bash
scripts/cli-netswitch.sh rule apply github
# 验证（主机）：curl -s https://api.github.com/meta >/dev/null && echo ok
# 验证出口 IP：容器内 curl ifconfig.me，应显示无线公网 IP
```

### 场景 D：撤销一条分流规则
```bash
scripts/cli-netswitch.sh rule clear github     # 清空该规则生效网卡并写回配置
# TUI 等价：选中规则按 c
```

### 场景 E：完全恢复原状
```bash
scripts/cli-netswitch.sh revert
scripts/cli-netswitch.sh status
```

### 场景 F：开机自动应用
```bash
scripts/cli-netswitch.sh install-systemd
```

### 场景 G：迁移到新机器 / 换网后重新探测
```bash
scripts/cli-netswitch.sh detect            # 先看看拟生成什么
scripts/cli-netswitch.sh detect --write    # 确认后写回（只更新 interfaces，规则不变）
scripts/cli-netswitch.sh apply
```

## 6. 日志与排查

### 6.1 运行日志

- **文件**：`logs/netswitch.log`，按大小轮转（2MB × 5 个）。
- **内容**：每条系统命令（`OK`/`FAIL`/`DRY-RUN`）、apply/revert、网卡开关、metric、策略路由、配置写回、CIDR 拉取、CLI/TUI 启动。
- **级别/目录**：默认 INFO（只记写操作与高层动作）；`NETSWITCH_LOG_LEVEL=DEBUG` 可含只读查询；`NETSWITCH_LOG_DIR` 可改目录。
- **查看**：`tail -f logs/netswitch.log`。

### 6.2 分流不生效？

按顺序排查：
1. `sysctl net.ipv4.ip_forward` 应为 `1`；
2. `sysctl net.ipv4.conf.all.rp_filter` 应为 `0` 或 `2`（严格模式会丢弃跨网卡转发的包）；
3. 容器网络若有 `-o <网卡> -j MASQUERADE` 显式规则，需覆盖目标出口网卡；
4. 用 `ip route get <目标IP> from <容器IP> iif <网桥名>` 观察路由走向；
5. `scripts/cli-netswitch.sh status` 查看规则的「生效网卡 / 是否已应用」。

### 6.3 完全恢复

`scripts/cli-netswitch.sh revert`：清理策略路由，并按 `data/config/state.json` 恢复原始默认路由与 metric。

## 7. 常见问题（FAQ）

详见 [faq.md](faq.md)。

## 8. 安全须知

- 高危操作前**务必**先跑 `preflight.sh`。
- 首次执行用 `--dry-run` 查看将执行的命令。
- 仅有一张网卡生效（唯一承载默认路由）时，程序**禁止关闭**它（`--force` 可强制，慎用）。
- 保持 `data/config/state.json` 不被误删（`revert` 依赖它）。
- 所有写操作默认要求确认：TUI 按 `y`；CLI 输入 `y`，脚本用 `-y` 跳过。
