# 变更日志（Changelog）

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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

### Changed
- 目录结构调整：Python 源码由 `src/` 改为 `src/python/`，测试代码定为 `src/test/`。
- `scripts/` 明确为 shell 便捷入口，统一设置 `PYTHONPATH` 后调用 `src/python` 中的 CLI/TUI 程序。
- 分流规则通用化：脚本名/模块名/配置键不硬编码 `github`，改为通用 `rules` 列表（初始默认规则名为 github）；模块 `github.py` 改为 `cidrs.py`。
- 强化容器覆盖为强制要求：分流必须同时覆盖主机与容器（网络命名空间）对外流量。
- 设计复盘（第 2 轮，总体设计/可配置化）：配置收敛为声明式（去除 apply.manage_* / systemd / match.ip_version 等冗余项）；`routing.backend` 全局化；`metric primary` 用 `metrics.preferred/fallback` 精确定义；修正 fwmark/table_id 默认值冲突、nft 整表原子替换幂等、文档冗余、目录不一致、preflight 硬编码回退。
- 版本与分支：版本号唯一来源改为 `src/python/netswitch/version.py`（当前 `0.1-dev`）；新增 `dev` 分支，日常开发在 `dev`，仅在发布正式版本时才合并到 `master`。

### Fixed
- `install.sh`：修复无 `sudo` 环境报 `sudo: command not found`——自动检测 root/sudo；依赖优先用户级 `pip install --user`，无提权能力时优雅跳过 systemd。

## 版本约定

- 版本号唯一来源：`src/python/netswitch/version.py` 的 `__version__`。
- 格式：正式版 `<major>.<minor>`（无后缀，如 `0.1`）；开发版 `<major>.<minor>-dev`（如 `0.2-dev`）。
- 分支：日常开发在 `dev`；发布正式版时才合并到 `master`。
- 发布流程：`scripts/release.sh --yes` —— 把 dev 版本转为正式版并归档本文件 → 合并 `dev` 到 `master` 并打 tag `vX.Y` → **自动把 dev 版本递增为 `X.(Y+1)-dev`**。
- 当前开发版本：`0.1-dev`（见 `version.py`）。
