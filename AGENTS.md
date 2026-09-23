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
- **分支策略**：日常开发在 **`dev`** 分支进行（它是 GitHub 默认分支）；仅在**发布正式版本**时才将 `dev` 合并到 `master`（经 `release.sh` 或 PR，见下）。
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

## GitHub 仓库与协作流程（分支保护 / PR）

CI 工作流（`.github/workflows/ci.yml`）的 job 名为 `test`（显示为 `CI / test`）。

### 分支模型

- `master`：**发布分支**（仅正式版本；受保护）。
- `dev`：**默认 / 开发分支**（日常开发都在这里）。

### 日常协作

- 日常提交直接推到 `dev`（受本地钩子与 CI 约束）。
- 需评审/多人协作时：从 `dev` 拉特性分支 → 开 PR → 合并回 `dev`。
- 发布：`scripts/release.sh --yes`（`dev` → `master`，打 tag `vX.Y`，自动递增 dev）。

### 一次性设置（GitHub 网页）

1. **默认分支设为 `dev`**：`Settings → General → Default branch → dev`（新增 PR 默认目标为 dev）。
2. **保护 `master`**：`Settings → Branches → Add branch protection rule`，pattern `master`：
   - 勾选 **Require a pull request before merging**（若仅你一人，可不再勾 Require approvals）。
   - 勾选 **Require status checks to pass before merging**，搜索并勾选 **`test`**（即 `CI / test`）。
   - （推荐）勾选 **Require branches to be up to date before merging**。
   - 勾选 **Do not allow force pushes** / **Do not allow deletions**。
   - ⚠️ 若继续用 `scripts/release.sh` **直推 master**：需允许绕过（把管理员加入 **bypass** 列表，或取消 “Do not allow bypassing the above settings”）；否则请用下面的 PR 方式发布。
3. （推荐）**保护 `dev`**：pattern `dev`，勾选 Require status checks → `test`。

### 若严格「必须 PR 才能合并 master」

不要用 `release.sh` 直推，改为：

```bash
# a) 推送开发分支
git push origin dev
# b) 在 GitHub 开 dev -> master 的 PR，等 test 通过后 Merge
# c) 打 tag 并递增 dev 版本
git checkout master && git pull
git tag -a vX.Y -m "release X.Y" && git push origin vX.Y
# 然后回到 dev，把 version.py 递增为 X.(Y+1)-dev（含 README/changelog 同步）并提交
```

本地钩子（可选，二选一）：

```bash
scripts/install-git-hooks.sh                                   # 版本化钩子（core.hooksPath）
# 或
pip install pre-commit && pre-commit install                    # pre-commit 框架
```
