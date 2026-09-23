# AGENTS.md —— AI Agent 与开发者协作规则

本文件约束所有在本仓库工作的 AI Agent 与开发者的行为。

## 提交前门槛（强制）

提交（commit / PR）前必须满足：

1. **测试全部通过**：`scripts/run-test.sh`（或 `make test`）必须全绿。
2. **更新并核对文档**：改动后同步更新 `docs/`：
   - 任何变更 → 更新 `changelog.md` 的 `[Unreleased]`。
   - 目录/文件增删 → 更新 `folder.md`。
   - 设计/配置/需求变化 → 同步 `requirements.md` / `technical.md` / `plan.md`。
   - 发现问题与决策 → 记录到 `review-findings.md`。
3. **版本一致**：`src/python/netswitch/version.py` 的 `__version__` 与 `README.md` 的「版本：」一致（`scripts/check-version.sh`）。
4. **分支正确**：日常开发在 `dev`；不得在 `master`/`main` 直接提交（`scripts/check-branch.sh`）。

一键核对：`scripts/check.sh`（测试 + 文档一致性 + 版本一致性）。

## 目录约定

| 内容 | 目录 |
|------|------|
| 源代码 | `src/`（Python 包在 `src/python/netswitch/`） |
| 测试 | `src/test/` |
| 配置模板 | `data/config/` |
| 脚本 | `scripts/` |
| 文档 | `docs/` |

## 版本与分支

- 版本号唯一来源：`src/python/netswitch/version.py` 的 `__version__`（当前 `0.1-dev`）。
- **版本约定**：正式版 `<major>.<minor>`（无后缀，如 `0.1`）；开发版 `<major>.<minor>-dev`（如 `0.2-dev`）。
- **分支策略**：日常开发在 **`dev`** 分支进行；仅在**发布正式版本**时才将 `dev` 合并到 `master`。
- **发布**：运行 `scripts/release.sh`（默认演练，`--yes` 执行）：把 dev 版本转为正式版并归档 changelog → 合并 `dev` 到 `master` 并打 tag `vX.Y` → **自动把 dev 版本递增为 `X.(Y+1)-dev`** 并推送。
- 文档中的版本号须与 `version.py` 保持一致；程序（TUI/status/日志）会显示版本号与程序更新时间。

## 本地 git 钩子（推荐安装）

```bash
scripts/install-git-hooks.sh     # 设置 core.hooksPath = scripts/git-hooks
```

- `pre-commit`：分支策略 + 版本一致性 + 文档核对（快检查）。
- `pre-push`：完整门槛 `scripts/check.sh`（测试 + 文档 + 版本）。
- 卸载：`git config --unset core.hooksPath`。

（也可改用 pre-commit 框架：`pip install pre-commit && pre-commit install`，hook 配置见 `.pre-commit-config.yaml`；两种方式二选一。）

## 常用命令

- 测试：`scripts/run-test.sh`（或 `make test`）
- 完整核对（测试 + 文档 + 版本）：`scripts/check.sh`（或 `make check`）
- 单独核对：`scripts/check-docs.sh` / `scripts/check-version.sh` / `scripts/check-branch.sh`
- 安装 git 钩子：`scripts/install-git-hooks.sh`
- 发布正式版：`scripts/release.sh`（演练） / `scripts/release.sh --yes`（执行）
- 本地预提交钩子（可选）：`pip install pre-commit && pre-commit install`
- 安装程序：`sudo scripts/install.sh`
- 干跑：`scripts/cli-netswitch.sh apply --dry-run`

## 禁止提交

- `data/config/config.json`、`data/config/state.json`、`data/config/cache-*.json`（运行时/本地文件，已在 .gitignore）。
- `logs/`（运行日志目录，已在 .gitignore）。

## GitHub 仓库一次性设置（分支保护）

CI 工作流（`.github/workflows/ci.yml`）的 job 名为 `test`（显示为 `CI / test`）。要让「测试不通过就不能合并」，需在 GitHub 上开启分支保护：

1. 仓库 `Settings → Branches → Add branch protection rule`。
2. `Branch name pattern` 填 `main`（或 `master`）。
3. 勾选 **Require status checks to pass before merging**。
4. 搜索并勾选 **`test`**（即 `CI / test`）。
5. （推荐）勾选 **Require branches to be up to date before merging**。
6. 保存。

本地预提交钩子（可选，commit 前自动跑）：

```bash
pip install pre-commit && pre-commit install
```
