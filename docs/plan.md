# 开发计划（Plan）

> 项目：netswitch
> 目标：脚本 + TUI 双接口，功能一致，可配置化多网卡管理

## 1. 功能清单（简要）

| 编号 | 功能 | 对应需求 |
|------|------|----------|
| F1 | 关闭 / 开启物理网卡 | FR1 |
| F2 | 调整网卡 metric（set / primary） | FR2 |
| F3 | 指定流量（初始 GitHub）指定出口网卡（主机+容器，可撤销） | FR3 |
| F4 | JSON 配置 + 自动探测 + 不写死 | FR4 |
| F5 | 脚本接口（CLI + scripts/*.sh） | FR5 |
| F6 | curses TUI（快捷键/高亮/实时拉取） | FR5 |
| F7 | 状态查询 | FR6 |
| F8 | apply / revert / dry-run | FR7 |
| F9 | systemd 开机自恢复 | FR8 |
| F10 | 网络自动探测与配置刷新（CLI detect + TUI 按钮） | FR9 |

## 2. 里程碑

### M0 — 项目骨架（已完成）
- [x] 读取并分析原始需求（已并入 docs/requirements.md）
- [x] 确认技术选型（Python3 标准库 / curses / JSON / runtime+systemd）
- [x] 编写 docs/ 全部文档（requirements / technical / plan / changelog / review-findings / test-plan / user-manuals / readme）

### M1 — 核心库（src/python/netswitch/）
- [x] `exec.py`：root 检查、dry-run、命令执行与日志
- [x] `model.py`：Interface / Route / Rule / NetState 数据模型
- [x] `config.py`：JSON 加载、默认值、探测填充、校验
- [x] `detect.py`：FR9 网络自动探测（物理网卡/IP/网关/metric/类型）
- [x] `ip.py`：`ip -j` 解析 + 命令构造
- [x] `iface.py`：F1/F2（网卡开关、metric 调整）
- [x] `cidrs.py`：F3 前置（CIDR 拉取/缓存/合并，通用）
- [x] `routing.py`：F3（nftables + iprule 双后端）
- [x] `status.py`：F7
- [x] `apply.py`：F8（apply/revert 编排）

### M2 — 脚本接口（scripts/）
- [x] `cli.py`：全部子命令（在 src/python 内）
- [x] `scripts/cli-netswitch.sh`（CLI 入口）与 `netswitch-tui`（TUI 入口）及 `*.sh` 包装脚本
- [x] `scripts/preflight.sh`：高危操作前预检（只读）
- [x] `scripts/install.sh`：一次性安装（装依赖/生成配置/预检，可选 --with-systemd）
- [x] `scripts/detect.sh`：重新探测网络并生成配置
- [x] 脚本自动设置 PYTHONPATH=src/python 后调用 CLI/TUI
- [x] `data/config/config.example.json`（用户自建 config.json）

### M3 — TUI（src/python/netswitch/tui.py）
- [x] 启动即拉取实时信息；动态 1~N 网卡卡片，有线/无线高亮、未连接显示
- [x] 网卡卡片、分流规则区、全局操作 + 快捷键绑定（字母显示在按钮上）
- [x] “重新探测网络”按钮 + 结果确认/写入弹层
- [x] 输入/确认弹层、异步 worker

### M4 — 系统集成与加固
- [x] `data/config/netswitch.service`（模板）+ `install-systemd`
- [x] 安全防护：最后网卡保护、出口检查、`--force`
- [ ] 测试代码（src/test/）：命令构造单测已完成（22 个通过）；netns 集成测试待做

### M5 — 文档与验收
- [ ] 更新 changelog / review-findings
- [ ] 校验 docs/ 全部文档与实现一致（含 user-manuals / readme 索引）
- [ ] 按 requirements.md 第 6 节验收清单逐项验证

## 3. 任务依赖

- F3（分流规则）依赖 `cidrs.py`（CIDR）与 `routing.py`（策略路由），后者依赖 `ip.py`。
- F5/F6 均依赖 M1 核心库完成后才能保证“功能一致”。
- M4 的安全防护需在 M1/M2 完成后叠加。

## 4. 风险与备注

- 规则 CIDR 数量可能较多（如 github 100+ 条），nftables 后端为首选（每条规则 1 条标记规则）；无 `nft` 自动回退 iprule。
- metric 变更仅运行时生效，DHCP 续约可能重置；由 systemd 开机重放兜底，后续可考虑 NetworkManager dispatcher。
- 关闭网卡后其路由消失、开启后依赖 DHCP 自动恢复，恢复过程有秒级延迟，属预期行为。

## 5. 当前状态

- 阶段：M1~M3 完成，M4 基本完成（缺 netns 集成测试），M5 待真实机器验收。
- 单测：22 个通过（`pytest src/test`）。
- 下一步：在目标机上跑 `preflight.sh` → `detect --write` → `apply --dry-run` → 真实验收；补充 netns 集成测试。
