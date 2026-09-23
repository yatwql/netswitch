#!/usr/bin/env bash
# release.sh —— 发布正式版本，并在发布后自动递增 dev 版本号。
#
# 版本约定：正式版 X.Y（无后缀）；开发版 X.Y-dev。发布后 dev 自动变为 X.(Y+1)-dev。
#   例：dev 0.1-dev --发布--> 正式 0.1，随后 dev 变为 0.2-dev。
#
# 流程：
#   1) 校验：在 dev 分支、工作区干净、当前版本为 X.Y-dev
#   2) 将版本号设为正式版 X.Y，并把 changelog 的 [Unreleased] 归档为 [X.Y]
#   3) 提交 dev；合并到 master（--no-ff）并打 tag vX.Y；推送 master 与 tag
#   4) 回到 dev，自动把版本号递增为 X.(Y+1)-dev 并推送
#
# 用法：
#   scripts/release.sh          # 仅演练（dry-run，默认）
#   scripts/release.sh --yes    # 真正执行
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VERSION_FILE="src/python/netswitch/version.py"
CONFIRM=0
for a in "$@"; do
  case "$a" in
    --yes|-y) CONFIRM=1 ;;
    --dry-run) CONFIRM=0 ;;
    *) echo "未知参数: $a（用法：release.sh [--yes]）"; exit 2 ;;
  esac
done

run() { if [ "$CONFIRM" = 1 ]; then "$@"; else echo "[dry-run] $*"; fi; }

ver() { python3 -c "import sys; sys.path.insert(0,'src/python'); from netswitch import version; print($1)"; }
setver() {
  python3 - "$VERSION_FILE" README.md docs/changelog.md "$1" <<'PY'
import pathlib, re, sys
vf, readme, changelog, v = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
p = pathlib.Path(vf); t = p.read_text(encoding="utf-8")
p.write_text(re.sub(r'__version__ = "[^"]*"', f'__version__ = "{v}"', t, count=1), encoding="utf-8")
p = pathlib.Path(readme); t = p.read_text(encoding="utf-8")
p.write_text(re.sub(r'(^> 版本：)[0-9]+\.[0-9]+(?:-dev)?', rf'\g<1>{v}', t, count=1, flags=re.M), encoding="utf-8")
p = pathlib.Path(changelog); t = p.read_text(encoding="utf-8")
p.write_text(re.sub(r'(- 当前开发版本：`)[^`]+(`)', rf'\g<1>{v}\g<2>', t, count=1), encoding="utf-8")
PY
}

CUR=$(ver "version.__version__")
REL=$(ver "version.release_version()")
NXT=$(ver "version.next_dev_version()")

echo "当前 dev 版本 : $CUR"
echo "正式版本      : $REL"
echo "发布后 dev    : $NXT"
echo

# 1) 校验
branch=$(git rev-parse --abbrev-ref HEAD)
[ "$branch" = "dev" ] || { echo "错误：必须在 dev 分支发布（当前 $branch）"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "错误：工作区不干净，请先提交"; exit 1; }
case "$CUR" in *-dev) ;; *) echo "错误：当前版本 $CUR 不是 -dev，无法发布"; exit 1;; esac
[ "$CONFIRM" = 1 ] || echo "（演练模式，未做任何修改；加 --yes 真正执行）"

# 2) 设为正式版 + 归档 changelog
run setver "$REL"
run python3 - docs/changelog.md "$REL" <<'PY'
import datetime, pathlib, re, sys
p = pathlib.Path(sys.argv[1]); v = sys.argv[2]
t = p.read_text(encoding="utf-8")
if f"## [{v}]" not in t:
    m = re.search(r"^## \[Unreleased\]\n(.*?)(?=^## )", t, re.S | re.M)
    if m:
        body = m.group(1).rstrip("\n")
        repl = (f"## [Unreleased]\n\n"
                f"## [{v}] - {datetime.date.today().isoformat()}\n{body}\n\n")
        p.write_text(t[:m.start()] + repl + t[m.end():], encoding="utf-8")
PY

# 3) 提交 dev -> 合并 master -> tag -> push
run git add -A
run git commit -m "release: $REL"
run git checkout master
run git merge --no-ff dev -m "release: $REL"
run git tag -a "v$REL" -m "release $REL"
run git push origin master
run git push origin "v$REL"

# 4) 回到 dev 自动递增
run git checkout dev
run setver "$NXT"
run git add -A
run git commit -m "chore: bump dev version to $NXT"
run git push origin dev

echo
echo "完成：正式版 $REL（tag v$REL）；dev 版本已递增为 $NXT"
