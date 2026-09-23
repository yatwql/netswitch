# 目录结构与文件说明（Folder）

> 项目：netswitch —— 多物理网卡切换与流量分流工具
> 本文列举项目目录结构，并逐个说明 docs / src / scripts 下每个文件的用途。

## 1. 顶层结构

```
switch/
├── README.md                # 软件简介与文档索引（根目录）
├── AGENTS.md                # AI Agent / 开发者协作规则（提交门槛）
├── Makefile                 # make test / check / install
├── pytest.ini               # pytest 配置（testpaths、markers）
├── .gitignore               # 忽略运行时/本地文件
├── .pre-commit-config.yaml  # 本地 pre-commit 钩子（pytest + 文档核对）
├── .github/workflows/ci.yml # GitHub Actions CI（测试 + 文档核对）
├── docs/                    # 文档
├── src/
│   ├── python/netswitch/    # Python 源程序（核心包）
│   └── test/                # 测试代码（pytest）
├── scripts/                 # shell 脚本（入口 + 便捷包装）
├── data/config/             # 配置模板与运行时文件
├── logs/                    # 运行日志（netswitch.log）
└── requirements-dev.txt     # 测试依赖
```

## 2. docs/ —— 文档

| 文件 | 简介 |
|------|------|
| `user-manuals.md` | 用户手册：安装、配置、常用命令速查、CLI/TUI 操作、典型场景 |
| `requirements.md` | 需求文档：功能/非功能需求、约束、验收标准 |
| `technical.md` | 技术设计：架构、配置 schema、核心机制、快捷键、安全 |
| `plan.md` | 开发计划：功能清单、里程碑（M0–M5）、进度 |
| `test-plan.md` | 测试计划：测试层次、用例清单、netns 集成、验收 |
| `changelog.md` | 变更日志（Keep a Changelog 风格） |
| `review-findings.md` | 自查记录：风险、决策、复盘与实现阶段缺陷 |
| `folder.md` | 本文件：目录结构与各文件说明 |
| `faq.md` | 常见问题（FAQ） |

## 3. src/python/netswitch/ —— 核心包

| 文件 | 简介 |
|------|------|
| `__init__.py` | 包标识，导入 `__version__` |
| `version.py` | 版本号与构建信息（唯一来源）：`__version__`、程序更新时间 |
| `exec.py` | 命令执行封装：root 检查、dry-run、`ExecError` 统一错误 |
| `log.py` | 运行日志：写入 `logs/netswitch.log`（按大小轮转） |
| `model.py` | 数据模型：`Interface`/`IfaceCfg`/`RuleCfg`/`CidrsCfg`/`Config` 等 dataclass |
| `config.py` | JSON 配置加载、默认值填充、探测填充、校验、`interfaces` 写回 |
| `detect.py` | 网络自动探测：物理网卡识别、有线/无线、连接状态、IP/网关/metric |
| `ip.py` | `ip -j` 命令解析与命令构造、物理网卡/连接状态判定、subnet/nft-set 命名 |
| `iface.py` | 网卡开关（最后网卡保护）与 metric 调整（set/primary） |
| `cidrs.py` | 通用 CIDR 来源：URL+JSON 字段拉取、缓存、extra 合并、IPv4 过滤 |
| `routing.py` | 分流规则策略路由：nftables/iprule 双后端、整表原子重建、清理 |
| `status.py` | 状态汇总与展示（网卡/默认路由/规则） |
| `apply.py` | apply/revert 编排、state.json 记录与恢复 |
| `cli.py` | argparse 命令行入口（status/detect/iface/metric/rule/apply/revert/install-systemd） |
| `tui.py` | curses TUI（网卡区/规则区/快捷键/底部输入） |

## 4. src/test/ —— 测试

| 文件 | 简介 |
|------|------|
| `conftest.py` | 把 `src/python` 加入 `sys.path`，便于 import netswitch |
| `test_config.py` | 配置加载/默认值/唯一性校验/冲突校验/规则网卡写回 |
| `test_detect.py` | 主网卡判定 |
| `test_ip.py` | 物理网卡识别、subnet、nft-set 命名、连接状态判定 |
| `test_iface.py` | metric 命令构造、最后网卡保护、up/down |
| `test_cidrs.py` | manual/url 来源、缓存回退、IPv4 过滤 |
| `test_routing.py` | 后端解析、nft 脚本生成、规则筛选、规则是否已应用 |
| `test_apply.py` | 默认路由快照与 state.json 读写 |
| `test_tui.py` | 字母键大小写归一化 |
| `test_version.py` | 版本号与程序更新时间格式 |
| `test_log.py` | 日志写入与命令记录 |
| `test_cli.py` | 命令行参数解析 |

## 5. scripts/ —— 脚本

| 文件 | 简介 |
|------|------|
| `install.sh` | 一次性安装：装依赖 + 生成配置并自动探测 + 预检（可选 `--with-systemd`） |
| `release.sh` | 发布正式版：dev→master（打 tag `vX.Y`），并自动把 dev 版本递增为 `X.(Y+1)-dev` |
| `preflight.sh` | 高危操作前预检（只读，不改变系统状态） |
| `check-docs.sh` | 文档一致性基础核对（提交门槛） |
| `run-test.sh` | 运行测试（无需 root） |
| `check.sh` | 提交前完整核对：测试 + 文档一致性 + 版本一致性 |
| `check-version.sh` | 版本一致性核对（`version.py` 与 `README.md` 一致） |
| `check-branch.sh` | 分支策略核对（禁止在 `master`/`main` 直接提交） |
| `install-git-hooks.sh` | 安装版本化 git 钩子（`core.hooksPath=scripts/git-hooks`） |
| `git-hooks/pre-commit` | 提交前钩子：分支策略 + 版本一致性 + 文档核对 |
| `git-hooks/pre-push` | 推送前钩子：完整门槛 `scripts/check.sh` |
| `cli-netswitch.sh` | CLI 统一入口（设置 PYTHONPATH 后调用 `netswitch.cli`） |
| `tui-netswitch.sh` | TUI 入口（调用 `netswitch.tui`） |
| `status.sh` | 查看状态（=`cli-netswitch.sh status`） |
| `detect.sh` | 重新探测网络并生成配置（=`cli-netswitch.sh detect`） |
| `iface-up.sh` | 开启网卡（=`cli-netswitch.sh iface up`） |
| `iface-down.sh` | 关闭网卡（=`cli-netswitch.sh iface down`） |
| `set-metric.sh` | 设置 metric（=`cli-netswitch.sh metric set`） |
| `rule-apply.sh` | 应用分流规则（=`cli-netswitch.sh rule apply`） |
| `rule-clear.sh` | 撤销分流规则（=`cli-netswitch.sh rule clear`） |
| `apply.sh` | 按配置应用全部（=`cli-netswitch.sh apply`） |
| `revert.sh` | 撤销全部改动（=`cli-netswitch.sh revert`） |

## 6. 其它

| 路径 | 简介 |
|------|------|
| `data/config/config.example.json` | 配置示例（复制为 config.json 使用） |
| `data/config/config.json` | 实际配置（install.sh 生成，用户维护） |
| `data/config/state.json` | 原始状态记录（apply 前生成，revert 依赖） |
| `data/config/cache-<规则名>.json` | 各规则 CIDR 缓存（运行时生成） |
| `data/config/netswitch.service` | systemd 服务单元模板（install-systemd 读取，含 `__REPO_ROOT__` 占位） |
| `logs/netswitch.log` | 运行日志（自动轮转；整个 `logs/` 已在 .gitignore） |
| `requirements-dev.txt` | 测试依赖：pytest、pytest-mock |

## 7. 工程 / CI 文件（提交门槛相关）

| 路径 | 简介 |
|------|------|
| `AGENTS.md` | AI Agent 与开发者协作规则，定义提交前门槛（测试全绿 + 文档核对） |
| `Makefile` | `make test` / `make check` / `make install` |
| `pytest.ini` | pytest 配置：`testpaths=src/test`、`integration` 标记 |
| `.pre-commit-config.yaml` | 本地 pre-commit 钩子：pytest + check-docs |
| `.github/workflows/ci.yml` | GitHub Actions CI：push/PR 时运行 pytest + 文档核对 |
| `.gitignore` | 忽略 `__pycache__`、`.pytest_cache`、`data/config/config.json`、`state.json`、`cache-*.json` |
