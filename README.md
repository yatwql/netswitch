# netswitch

> Debian 多物理网卡切换与流量分流工具。同时提供**脚本（CLI）**与 **TUI** 两种功能一致的操作方式。
> 版本：0.2-dev（开发中，`dev` 分支）｜运行时**零第三方依赖**（仅 Python 标准库）

## 1. 一句话简介

在拥有**多张物理网卡**和大量容器的 Debian 主机上，安全地**开关网卡**、**调整 metric 优先级（设主网卡）**、并把**指定流量**（初始为 GitHub，主机与容器对外访问都算）导流到指定网卡——全部可配置、带确认、可撤销。

## 2. 核心能力

| 能力 | 说明 |
|------|------|
| 网卡开关 | 临时关闭 / 开启任意物理网卡；**仅剩一张生效网卡时禁止关闭**（`--force` 才可应急覆盖） |
| metric / 主网卡 | 设指定值或「设为主网卡」（metric 最小者为主）；主网卡在 TUI 中高亮为绿色 |
| 流量分流 | 按**规则**（初始 GitHub）把指定流量导向某张网卡，**主机与容器对外流量都覆盖** |
| 规则可撤销 | 撤销即清空该规则的生效网卡并**写回配置**（持久化，重启/apply 不复活） |
| 全可配置化 | 网卡/网关/metric/规则/后端全在 `data/config/config.json`；网卡信息**运行时自动探测**，不写死 |
| 自动探测 | 一键重新探测本机网卡并刷新配置（CLI `detect --write` / TUI `d`） |
| 双接口一致 | CLI 与 TUI 共享同一核心；按键/命令一一对应 |
| 安全 | 破坏性操作**默认确认**、`--dry-run` 干跑、原始状态记录、`revert` 撤销 |
| 运行日志 | 所有命令与动作写入 `logs/netswitch.log`（按大小轮转） |
| 开机自恢复 | systemd 服务开机自动应用配置 |

## 3. 技术特点

- **零第三方运行时依赖**：只用 Python 3 标准库（`json` 配置、`curses` TUI、`ipaddress`、`logging`）。
- **系统层**：基于 `iproute2`（必需）+ `nftables`（可选，无 `nft` 自动回退 `ip rule` 后端）。
- **动态探测**：物理网卡、类型（有线/无线）、连接状态（已连接/未插网线/已关闭）、IP、网关、metric 均运行时获取。
- **策略路由覆盖容器**：`PREROUTING`（转发）+ `OUTPUT`（本机）打标 + 独立路由表，容器 NAT 出网同样被分流。

## 4. 快速开始

```bash
# 1) 首次安装（一次性）：检查环境 + 生成配置并自动探测 + 预检
sudo scripts/install.sh
# 如需开机自恢复：
sudo scripts/install.sh --with-systemd

# 2) 日常使用（CLI 或 TUI 二选一，不必都跑）
scripts/cli-netswitch.sh apply --dry-run   # 可选：先看将执行的命令
sudo scripts/cli-netswitch.sh apply        # 方式 A：CLI 应用
scripts/tui-netswitch.sh                   # 方式 B：TUI 界面
```

> 常用命令与 TUI 按键速查见 [docs/user-manuals.md](docs/user-manuals.md)；用法疑难见 [docs/faq.md](docs/faq.md)。

## 5. 文档索引

| 文档 | 内容 | 面向 |
|------|------|------|
| **README.md**（本文件） | 软件简介与文档索引 | 所有人 |
| [docs/user-manuals.md](docs/user-manuals.md) | 用户手册：安装、配置、常用命令速查、CLI/TUI 操作、场景 | 使用者 |
| [docs/faq.md](docs/faq.md) | 常见问题（FAQ） | 所有人 |
| [docs/requirements.md](docs/requirements.md) | 需求文档：功能/非功能需求、约束、验收标准 | 所有人 |
| [docs/technical.md](docs/technical.md) | 技术设计：架构、配置 schema、核心机制、日志、安全 | 开发者 |
| [docs/test-plan.md](docs/test-plan.md) | 测试计划：用例清单、netns 集成、手动验收 | 开发者/测试 |
| [docs/plan.md](docs/plan.md) | 开发计划：里程碑、功能清单、进度 | 开发者 |
| [docs/changelog.md](docs/changelog.md) | 变更日志 | 所有人 |
| [docs/review-findings.md](docs/review-findings.md) | 自查记录：风险、决策、复盘 | 开发者 |
| [docs/folder.md](docs/folder.md) | 目录结构与各文件说明 | 所有人 |

## 6. 环境要求

- Debian Linux（systemd + iproute2）
- root 权限（写操作）
- Python 3.9+（**无第三方运行时依赖**，仅标准库）
- 可选：`nftables`（无则自动回退）；`make`/`pre-commit`（仅开发便利）

## 7. 工程与质量

- **测试**：`src/test/` 下 44 个 pytest 单测，`scripts/run-test.sh`。
- **提交门槛**：`AGENTS.md` 规定「测试全绿 + 文档核对」；CI 与本地钩子自动执行；`scripts/check.sh` 一键核对。
