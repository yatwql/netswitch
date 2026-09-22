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
   - 核对索引：`scripts/check.sh`（测试 + 文档一致性）。

## 目录约定

| 内容 | 目录 |
|------|------|
| 源代码 | `src/`（Python 包在 `src/python/netswitch/`） |
| 测试 | `src/test/` |
| 配置模板 | `data/config/` |
| 脚本 | `scripts/` |
| 文档 | `docs/` |

## 常用命令

- 测试：`scripts/run-test.sh`（或 `make test`）
- 完整核对（测试 + 文档）：`scripts/check.sh`（或 `make check`）
- 本地预提交钩子：`pip install pre-commit && pre-commit install`
- 安装：`sudo scripts/install.sh`
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
