# 变更日志（Changelog）

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

**配置驱动的规则增删 + 域名/通配符（FR11）**

- TUI 新增菜单项 `n`（新增规则）/ `x`（删除规则）；CLI 新增 `rule add` / `rule remove`（含 `--name` / `--interface` / `--dry-run`）。
- **只写配置，不触碰网络**：新增的规则在下一次 `apply` 才生效；删除只从配置移除，已生效的分流产物等下次 `apply` 按签名清理。
  对比：`e`（选出口）/`c`（撤销分流）/`p`/`a`/`r` 仍会立即作用到网络（已在文档/TUI 帮助与提示中明确区分）。
- 输入支持 IP、CIDR、域名、通配符域名（`*.example.com`）与直接粘贴 URL（自动取主机名）；
  可一次输入多个（逗号/空格/分号分隔），无法识别的项直接报错（不静默丢弃）。
- **域名在 apply 时解析**（A + AAAA → `/32`/`/128`），结果带 TTL 缓存与失败回退；
  `*.example.com` = apex + DNS 通配符探测（随机子域查询，可用 `cidrs.wildcard_probe: false` 关闭）；
  文档明确“通配符 ≠ 所有子域”，需精确覆盖时显式列出子域。
- 缓存结构升级为 v2（`url_cidrs` / `domain_patterns` / `domain_cidrs` 分段 + 各自时间戳，
  域名列表变更会强制重解析）；旧缓存仍可作为 URL 部分回退使用。
- 配置写入安全化：`config.add_rule` / `remove_rule` / `next_rule_name`（自动命名 `custom-N`）；
  写入前校验（规则名/网段/域名/出口网卡/地址族），写后用 `config.load()` 重新校验，**失败回滚文件原文**。
- 规则与域名上限：每条规则最多 64 个域名（防呆），网段/域名超出或写法非法一律拒绝。

**IPv6 双栈分流（FR10）**

- 分流规则支持 **IPv6**：`cidrs` 来源（URL / `extra`）现在同时保留 v4 与 v6（`cidrs._finish` 不再丢弃 v6，并统一规范化）。
- 新增 `routing.ip_versions`（默认 `["v4"]`，**向后兼容**）与规则级覆盖 `rules[].cidrs.ip_versions`（可为单条规则开/关某个族）。
- 新增 `interfaces[].gateway6`（缺省自动探测；探测值写入 `detect --write` 的配置片段）；TUI/status 展示网卡 v6 地址/网关与规则的地址族（`族=v4+v6`）。
- 三个后端均支持 v6：nftables（`ipv6_addr` 集合 + `ip6 daddr` + `ip -6 rule fwmark`）、iprule（`ip -6 route ... table N` + `ip -6 rule add to <v6>`）、mainroute（`ip -6 route replace ... proto 200`）。
- **降级而不失败**：系统未启用 IPv6、出口网卡无 v6 网关、或内核缺 `CONFIG_IPV6_MULTIPLE_TABLES` 时，告警并跳过 v6，v4 分流照常生效（`mainroute` 不依赖多路由表，v6 仍可分）。
- 能力探测新增地址族维度：`ip.family_available(6)`（只读）、`policy_routing_supported(family)`；v6 不可用时不做任何 `ip -6` 写操作或查询。
- 清理同样只动自己的产物：v6 明细路由按 `proto 200` + 出口网卡 + 地址族判定；`ip -6 rule` / `ip -6 route flush table` 按同一签名清理（netns 用例 I-12/I-13）。

### Security / Fixed（安全与正确性）
- **修复误删路由（严重）**：`clear_rules` 不再无条件执行 `ip route del <cidr> table main`。现在只删除“主表中确实存在、`proto = 200`（本程序标记）、且出口网卡匹配”的条目，不再误删内核直连路由、他人 `proto static` 路由或 VPN 路由（netns 实测旧实现会删掉它们）。同时清理只针对 `mainroute` 后端已启用规则，不再受禁用/被排除规则影响。
- **修复 systemd 单元不可用**：`ExecStart` 由 `apply --yes --config .../config.yaml`（扩展名错 + 顶层选项位置错，实测 exit=2）改为 `--config .../config.json apply --yes`，并新增 `WorkingDirectory`；`install-systemd` 写入前会用同一套 argparse 校验 `ExecStart`。
- **配置白名单校验**：`routing.backend` 必须是 `auto|nftables|iprule|mainroute`（拼错不再静默回退）；`nft_table`/`rules[].name` 限字符集与长度（且归一化后不得重名）；`table_id` 限定 200..252（避开内核保留表 253/254/255，防止 `flush` 主表）；`fwmark` 限定 1..0xFFFFFFFF；`cidrs.source`/`ttl_hours` 校验。
- **数据路径约束**：`state_file`/`cidrs.cache_file` 必须落在数据目录（默认 `<仓库>/data`，可用 `NETSWITCH_DATA_DIR` 覆盖）内，拒绝绝对路径越界与 `..` 穿越；相对路径统一按仓库根解析，不再随当前工作目录漂移。

### Fixed（健壮性 / 一致性）
- **只读能力探测**：策略路由能力改为只读 `ip -j rule show` 探测（旧实现会临时增删测试路由，dry-run 也会写系统，并发时还会因 `File exists` 误判）；非 root 返回“未知”，`status` 明确提示。
- **状态语义**：`state.json` 的 `original_defaults` 只在首次 apply 记录（旧实现每次 apply 都覆写，导致 revert 恢复到“被改过”的 metric）；新增 `backend`/`tables` 字段；revert 成功后删除状态文件；状态文件损坏时按“无记录”容错。
- **预检 + 回滚**：`routing.apply_rules()` 先做只读预检（出口网卡存在、网关可解析、CIDR 合法、`iprule` 所需 `ip rule` 数不超上限），通过后才清理重建；重建中出错会 best-effort 回滚清理，不再留下半成品。
- **清理历史残留**：清理按 `pref ∈ [20000, 32000)` + 派生表号区间识别本程序产物，并接受 state 里的历史表号 —— 规则被删除/改名/换表号、或切换后端后都能清干净（内核保留表永不被 flush）。
- **CIDR 一次拉取 + 规范化**：同一次 apply 每个 CIDR 源只拉取一次（旧实现每条规则最多请求 4 次，且 nft set 与实际规则可能来自不同批次）；CIDR 统一规范化（`1.2.3.4/24`→`1.2.3.0/24`，裸地址→`/32`），非法项（域名、`/999` 等）丢弃并告警；下载体量上限 8MB 且必须是 JSON 对象。（IPv6 网段不再被丢弃，见上文 FR10。）
- **`metric set/primary` 修正**：`set_metric` 现在会删掉该网卡**全部**默认路由再重建；支持 `metric=None`（恢复不带 metric 的原始默认路由）；`metric primary <iface>` 在目标网卡不在 `config.interfaces` 时会按探测结果补齐，不再静默失效。
- **`rule apply` 语义**：改为按配置声明**全量重建**（旧行为会先把其它规则清掉）；CLI/TUI 确认文案同步说明。
- **CLI 健壮性**：`metric set` 缺数值时报参数错误（退出码 2）而非 `TypeError`；顶层兜底捕获未预期异常（人话提示 + 日志 traceback，退出码 1）。
- **物理网卡识别**：优先用 sysfs `/sys/class/net/<if>/device` 判定，排除 VLAN 子接口（`enp2s0.100`）与 `dummy`/`wg`/`ppp`/`macvlan` 等虚拟前缀；同一网卡多条默认路由取 metric 最小者。
- **日志可用性**：仓库 `logs/` 不可写（如被 root 创建）时自动回退到 `$XDG_STATE_HOME/netswitch/logs` 或临时目录并在 stderr 提示一次（旧实现静默不写日志）；启动行新增发起用户；`status` 打印当前日志路径。
- **TUI 体验与开销**：长错误改用可折行的**错误弹层**（状态栏只有一行，旧实现直接截断）；执行前显示“执行中…”；每个刷新周期只探测一次网卡并把快照传给 `config.load`，`mainroute` 后端下主表只 dump 一次；配置非法时 TUI 仍可打开并显示原因。
- **status 健壮性**：单条规则状态查询失败不再让整个 `status` 报错退出。

### Changed（工程 / 测试 / 文档）
- **CI 升级**：Python 3.9/3.11/3.13 矩阵上直接跑 `scripts/check.sh`（补上此前遗漏的版本一致性核对），并新增 shell 语法检查（`bash -n`）、`compileall` 与 `pyproject.toml` 解析。
- **文档门禁自动化**：`check-docs.sh` 新增「`folder.md` 必须覆盖 src/scripts/data/config 与工程文件」与「改动了 `src/`/`scripts/` 就必须同步 `docs/changelog.md`」两项检查（AGENTS.md 的人工要求变成机器门禁）。
- **新增 `pyproject.toml`**：包元数据、`netswitch`/`netswitch-tui` 入口点与 dev 依赖；版本仍以 `version.py` 为唯一来源。⚠️ 仅做了 TOML 解析与元数据校验（CI 与本次改动环境均未安装 setuptools，未实际构建/安装验证）；`install-systemd`、`data/config/*` 仍按“仓库内运行”假定，打包安装后的 systemd 集成不在支持范围。
- **测试**：新增 netns 集成测试（`src/test/test_integration_netns.py`，用 `unshare -rn`，**无需 root**，环境不支持自动 skip）与 `test_status.py`；重写 `test_routing`/`test_config`/`test_apply`/`test_iface`/`test_ip`/`test_cidrs`/`test_cli`/`test_log`/`test_tui`/`test_detect`。用例数 44 → **266**（含 5 个 netns 集成用例：v4/v6 清理安全、apply→clear 闭环、dry-run 不改系统）。
- **preflight.sh**：能力探测在“探测项已存在（并发预检）”时不再误报失败；文档明确该脚本会临时增删一条测试路由并立即删除。
- **文档同步**：requirements/test-plan/technical/plan/review-findings/user-manuals/faq/folder/README 全量对齐本轮改动。

## [0.1] - 2026-09-23

### Added
- 项目立项：双物理网卡切换与 GitHub 分流工具（netswitch）。
- 需求分析：完成原始需求的读取与整理（并入 requirements.md）。
- 技术设计：完成总体架构、配置 schema、核心机制（网卡开关 / metric / 策略路由）、安全防护设计（technical.md）。
- 开发计划：定义 M0–M5 里程碑与功能清单（plan.md）。
- 初次设计自查：记录风险点与决策（review-findings.md）。
- 测试计划：新增 docs/test-plan.md（测试层次、用例清单、netns 集成、手动验收）。
- 预检脚本：新增 scripts/preflight.sh（高危操作前只读预检：环境/网卡/安全/后端/源可达性）。
- 用户手册：新增 docs/user-manuals.md（安装、配置、脚本/TUI 操作、场景、FAQ）。
- 软件简介与索引：新增 docs/readme.md（简介 + 文档索引）。
- 网络自动探测（FR9）：新增 detect 命令、detect.py 模块、detect.sh 脚本与 TUI「重新探测网络」按钮，自动识别运行机器网卡/网关/metric 并刷新 interfaces 配置。
- TUI 快捷键：每个按钮绑定单键快捷键（字母显示在按钮上），全局无冲突，见 technical.md §7.1。
- 多网卡支持与 TUI 实时信息：支持 1~N 张物理网卡（数量不固定）；TUI 进入即拉取实时信息，有线/无线高亮，未插网线/未关联显示“未有连接”。
- 首次实现：完成 `src/python/netswitch/` 核心库、CLI、`scripts/` 包装、TUI、`systemd/` 服务、示例配置与依赖清单；22 个单测通过。
- 安装脚本：新增 `scripts/install.sh`（一次性：装依赖 + 生成配置并自动探测 + 预检，可选 `--with-systemd`）。
- 目录调整：systemd 服务单元模板由 `systemd/netswitch.service` 移至 `data/config/netswitch.service`（配置模板归 data/config），删除 `systemd/` 目录；`install-systemd` 改为读取该模板并替换 `__REPO_ROOT__`。
- 提交门槛：新增 `AGENTS.md`（协作规则）、`.github/workflows/ci.yml`（CI：pytest+文档核对）、`.pre-commit-config.yaml`（本地钩子）、`Makefile`、`pytest.ini`、`.gitignore`、`scripts/check-docs.sh`。
- 测试脚本：新增 `scripts/run-test.sh`（跑测试）与 `scripts/check.sh`（测试 + 文档核对）；Makefile / CI / pre-commit / AGENTS.md 统一改为调用脚本。
- 依赖极简化：PyYAML 改用 apt 的 `python3-yaml`（不依赖 pip）；textual 改为可选（仅 TUI，装不上 CLI 照常）；`install.sh` 与 `netswitch-tui` 相应调整。
- 零外部依赖重构：配置 YAML→JSON（标准库 json）、TUI textual→curses（标准库）；移除 PyYAML 与 textual 两个第三方依赖；`config.json`/`config.example.json`、`install.sh`/`preflight.sh`/`netswitch-tui`、CI、测试与全部文档同步更新。
- 脚本重命名：`scripts/netswitch` → `scripts/cli-netswitch.sh`，`scripts/netswitch-tui` → `scripts/tui-netswitch.sh`（包装脚本、文档、AGENTS.md 同步）。
- TUI 规则管理：数字键仅选网卡；`g` 切换规则、`e` 选生效网卡并写回 `config.json`、显示生效网卡与已应用状态；默认示例配置 `rules` 清空（github 仅作文档示例）。
- TUI 网卡配色：主网卡绿色 / 其他网卡蓝色 / 被禁止或不可用红色。
- 操作确认：TUI 与 CLI 对网卡开关、metric 转换、规则改写、apply/revert 增加确认步骤；CLI 支持 `-y/--yes`，非交互需显式 `-y`，systemd 服务自动带 `--yes`。
- CLI 确认可跳过：除 `-y/--yes` 外支持环境变量 `NETSWITCH_YES=1`（默认仍为确认）；包装脚本透传参数。
- TUI 退出与按键：`q` 或 `Ctrl+C` 退出（不再用 Esc，避免与 F5 等序列冲突）；所有字母快捷键大小写不敏感；显式启用 `keypad`，`f`/`F5` 刷新。
- TUI 自动刷新与开关修正：以**管理状态**（admin up）判断开/关，修复“开启网卡后界面不变”；界面每 ~1.5s 自动刷新，`f`/`F5` 手动刷新。
- TUI 导航：`↑`/`↓` 在网卡与规则间移动选中项（统一游标）；数字仍仅选网卡、`g` 切规则。
- 网卡关闭保护：仅有一张物理网卡生效（唯一承载默认路由）时**禁止关闭**该网卡（默认拒绝，`--force` 为显式应急覆盖）。
- 分流规则撤销：TUI `c` 与 CLI `rule clear` 现在会清空该规则的 `interface` 并**写回 config.json**（撤销持久化，避免下次 apply 又重新生效）。
- TUI 稳定性与可用性：每轮按键/自动刷新独立 try/except（单次出错不再终止刷新循环）；Enter 显示操作提示而非“未绑定按键”；状态栏增加“最后刷新 时间”。
- TUI Enter 默认动作：`Enter` 对选中项执行默认操作（网卡=**设为主网卡**、规则=应用）。
- TUI Esc：按 `Esc` **返回主页面并刷新**（不做退出，避免与 F5 序列冲突）；输入/确认时 `Esc` 取消当前输入、`Ctrl+C` 退出。
- TUI Enter 行为调整：Enter 仅在选中**网卡**时执行默认动作（设为主网卡）；选中规则时仅提示（不再直接应用）。输入行改为逐字符读取以支持中途 `Esc`/`Ctrl+C`。
- 运行日志：新增 `logs/` 目录与 `logs/netswitch.log`（按大小轮转）；每条系统命令（`OK`/`FAIL`/`DRY-RUN`）与 apply/revert、网卡开关、metric、策略路由、配置写回、CIDR 拉取、CLI/TUI 启动均记录。
- 清理根目录：删除无用的 `requirements.txt`（无运行时依赖且无人引用）与缓存目录（`.pytest_cache/`、`__pycache__/`，已在 .gitignore）。
- 文档精简：删除 `基本需求.txt`（内容已整合进 requirements.md，过时的 ip 输出丢弃）；requirements.md 去掉机器专属的网卡/路由信息，改为“运行时动态探测”表述。
- README 更新：重写 docs/readme.md，反映最新能力（零第三方依赖、自动探测、主网卡/配色、确认与撤销、日志、CLI/TUI 按键、测试与提交门槛）。
- 文档组织：`README.md` 移到仓库根（删除原 `docs/readme.md`）；新增 `docs/faq.md`（常见问题）；「常用命令」并入 `docs/user-manuals.md`；check-docs/文档索引同步更新。
- README 精简：移除与 folder.md / faq.md 重复的「目录结构」「常见问题速查」两节，改为在文档索引与指引中引用。
- 用户手册梳理：以用户视角整体重组（能做什么 / 快速上手 / 配置 / 命令与按键速查 / 场景 / 日志与排查 / FAQ / 安全）；「日志」说明由 README 移入 user-manuals.md，README 再精简为 7 节。
- .gitignore：忽略整个 `logs/` 目录（运行日志，程序运行时自动创建），移除 `logs/.gitkeep`。
- TUI 配色：分流规则生效时，其出口网卡在规则行「生效网卡」与物理网卡列表均显示为**黄色**（新增黄色配色对）。
- 无线网卡 SSID：探测并展示无线网卡的当前 SSID（`iw dev <if> link`，回退 `iwgetid -r`），在 TUI 网卡行与 `status` 中显示；未连接显示 `-`。
- TUI 标题栏：显示当前主机名（`netswitch · <主机名> · 网卡切换控制台`）。
- `status` 输出：首行显示当前主机名。
- 主机名配色与刷新时间：TUI 标题栏主机名显示为**蓝色**；状态栏「最后刷新」时间改为 `yyyyMMdd HH:mm:ss`（含日期）。
- 版本信息统一：新增 `src/python/netswitch/version.py`（版本号 `0.1-dev`，唯一来源）；TUI（标题下方）、`status`、日志均显示**版本号**与**程序更新时间**（本地时区 `yyyyMMdd HH:mm:ss +ZZZZ`）。
- 版本策略与发布脚本：正式版 `X.Y`（无后缀）、开发版 `X.Y-dev`；新增 `scripts/release.sh`（dev→master + 打 tag + **自动递增 dev 版本**）及版本 helper（`release_version` / `next_dev_version`）。
- 规则落地到脚本与钩子：新增 `scripts/check-version.sh`（版本一致性）、`scripts/check-branch.sh`（分支策略）、`scripts/install-git-hooks.sh` 与 `scripts/git-hooks/{pre-commit,pre-push}`；`scripts/check.sh` 纳入版本一致性；`AGENTS.md` 明确提交门槛、版本/分支规则与钩子安装。
- AGENTS.md 补充 GitHub 协作：分支模型（`dev` 默认/开发、`master` 发布/受保护）、PR 流程与分支保护设置步骤（含 `release.sh` 直推与「必须 PR」两种处理）。
- TUI 状态栏：刷新时间增加时区（`yyyyMMdd HH:mm:ss +ZZZZ`），与标题栏版本/程序更新时间格式一致。
- 缺省分流规则：`config.example.json` 自带一条 `github` 规则（出口网卡留空）；新增 `cli-netswitch.sh seed-defaults`（`rules` 为空时写入）与 `config.seed_default_rules()`；`install.sh` 自动调用，新装机不再“无规则”。
- 策略路由能力诊断：`preflight.sh` 新增「策略路由能力」项（探测自定义路由表 + `ip rule`）；`routing._run_or_hint()` 在 `RTNETLINK: Operation not supported` 时给出明确原因与提示（内核 `CONFIG_IP_MULTIPLE_TABLES` / 受限容器）；`faq.md` / `technical.md` 同步说明。
- 新增 **mainroute 后端**（回退方案）：不支持策略路由时，在主路由表按目标网段加明细路由分流（不需多路由表/`ip rule`）；`backend: auto` 自动回退；`preflight.sh` 同时检测主表路由能力；`status` 显示后端说明。
- 修复 mainroute 生效判定与 TUI 可见性：`routing.rule_applied()` 改为**按后端判断**（mainroute 用主表中 `proto 200` 的明细路由判定，之前写死查独立路由表导致永远“未应用”）；`apply_rules()` 返回应用条数并支持告警回调（TUI 状态栏可见，不再被 `print` 淹没）；TUI 增加 **root 检查**；`status` 规则状态显示「已应用/未应用」。
- 修复 `clear_rules` 在**不支持策略路由**的机器上误导操作：仅在后端使用且内核支持时才清理 `ip rule`/独立路由表（之前无条件 `ip rule show` 会 EOPNOTSUPP，导致 apply 在清理阶段就失败）；nft 删表仅在 nftables 后端执行；`exec.run` 将 `check=False` 的失败降为 **DEBUG**，日志不再刷 “Operation not supported / Could not process rule” 噪声。
- 权限与用户可见性：`version.user_line()`/`login_name()`（`sudo` 时取 `SUDO_USER`）；TUI 标题栏（非 root 显示「⚠ 非root·只读」）与版本行、`status`、`preflight.sh` 均显示**当前登录用户/运行身份**；非 root 启动 TUI 即时提示用 sudo。

### Changed
- 目录结构调整：Python 源码由 `src/` 改为 `src/python/`，测试代码定为 `src/test/`。
- `scripts/` 明确为 shell 便捷入口，统一设置 `PYTHONPATH` 后调用 `src/python` 中的 CLI/TUI 程序。
- 分流规则通用化：脚本名/模块名/配置键不硬编码 `github`，改为通用 `rules` 列表（初始默认规则名为 github）；模块 `github.py` 改为 `cidrs.py`。
- 强化容器覆盖为强制要求：分流必须同时覆盖主机与容器（网络命名空间）对外流量。
- 设计复盘（第 2 轮，总体设计/可配置化）：配置收敛为声明式（去除 apply.manage_* / systemd / match.ip_version 等冗余项）；`routing.backend` 全局化；`metric primary` 用 `metrics.preferred/fallback` 精确定义；修正 fwmark/table_id 默认值冲突、nft 整表原子替换幂等、文档冗余、目录不一致、preflight 硬编码回退。
- 版本与分支：版本号唯一来源改为 `src/python/netswitch/version.py`（当前 `0.1-dev`）；新增 `dev` 分支，日常开发在 `dev`，仅在发布正式版本时才合并到 `master`。

### Fixed
- `install.sh`：修复无 `sudo` 环境报 `sudo: command not found`——自动检测 root/sudo；依赖优先用户级 `pip install --user`，无提权能力时优雅跳过 systemd。
- 修复发布时 pre-push 失败：`test_version_value` 硬编码 `0.1-dev`，改为校验版本格式（`X.Y` / `X.Y-dev`），使发布（版本变为 `X.Y`）时测试仍通过。

## 版本约定

- 版本号唯一来源：`src/python/netswitch/version.py` 的 `__version__`。
- 格式：正式版 `<major>.<minor>`（无后缀，如 `0.1`）；开发版 `<major>.<minor>-dev`（如 `0.2-dev`）。
- 分支：日常开发在 `dev`；发布正式版时才合并到 `master`。
- 发布流程：`scripts/release.sh --yes` —— 把 dev 版本转为正式版并归档本文件 → 合并 `dev` 到 `master` 并打 tag `vX.Y` → **自动把 dev 版本递增为 `X.(Y+1)-dev`**。
- 当前开发版本：`0.2-dev`（见 `version.py`）。
