# 常见问题（FAQ）

> 面向使用者与运维。命令统一用 `scripts/cli-netswitch.sh <子命令>`（TUI 为 `scripts/tui-netswitch.sh`）。
> 相关文档：[user-manuals.md](user-manuals.md)、[technical.md](technical.md)。

## 安装与权限

**Q1：提示需要 root？**
写操作（开关网卡、改 metric、改规则、apply/revert、install-systemd）需要 root。用 `sudo`，或先 `sudo -i`。只读的 `status`/`detect`（不加 `--write`）无需 root。

**Q2：脚本/自动化里总是要求确认，怎么办？**
默认确认。跳过方式：加 `-y`/`--yes`，或设环境变量 `NETSWITCH_YES=1`。非交互（非 TTY）环境若未跳过会**拒绝执行**，以防误改网络。

**Q3：需要装什么依赖？**
**不需要任何第三方 Python 依赖**（仅标准库）。系统侧只需 `iproute2`；`nftables` 可选。`sudo scripts/install.sh` 会检查环境、生成配置并自动探测网卡。

**Q4：没有 `nft` 命令？**
自动回退 `iprule` 后端（纯 `ip rule`），功能一致，只是规则条数多一些。可在配置里显式指定 `"routing": { "backend": "iprule" }`。

## 配置

**Q5：配置文件在哪？格式？**
`data/config/config.json`（JSON，**不支持注释**）。首次可用 `sudo scripts/install.sh` 生成，或 `cp data/config/config.example.json data/config/config.json`。字段说明见 [user-manuals.md](user-manuals.md) §3。

**Q6：网卡名/网关/metric 要手写吗？**
不用。程序**运行时自动探测**；配置里 `interfaces[].gateway`/`metric` 均可省略。换机器/换网后可 `scripts/cli-netswitch.sh detect --write` 刷新。

**Q7：怎么新增一条分流规则（如 GitHub）？**
在 `config.json` 的 `rules` 数组里加一条：
```json
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
```
`table_id`/`fwmark` 不填会自动分配且保证唯一（`table_id` 必须 200..252，避开内核保留表 253/254/255；`fwmark` 不能为 0）；`interface` 也可以后续在 TUI 里用 `e` 选择。配置里写错 `backend`、规则名、表号或路径时会在启动阶段直接报错，不会带病改动网络。

**Q8：默认有分流规则吗？**
有。`config.example.json` 自带一条 **`github` 缺省规则**（分流 github.com 相关流量），**出口网卡留空**。新装机后：在 TUI 选中该规则按 `e` 选出口网卡即可。若配置里 `rules` 为空（如旧配置），运行 `scripts/cli-netswitch.sh seed-defaults` 写入缺省规则。

## 操作

**Q9：怎么把某张网卡设为主网卡？**
TUI：选中网卡按 `m`（或 `Enter`）→ `y` 确认。CLI：`sudo scripts/cli-netswitch.sh metric primary <网卡名>`。原理是把该网卡 metric 设为最小（默认 100，其余 600）。

**Q10：怎么撤销某条分流规则？**
TUI：选中规则按 `c`；CLI：`sudo scripts/cli-netswitch.sh rule clear <规则名>`。两者都会清空该规则在 `config.json` 里的 `interface`（**持久撤销**），下次 `apply` 不会复活。

> `rule apply <规则名>` 的语义是“**按配置重建全部规则**”（声明式），单独应用一条不会把其它规则清掉；`<规则名>` 只用于确认提示。

**Q11：怎么完全恢复原状？**
`sudo scripts/cli-netswitch.sh revert`：清理本程序的分流产物并按 `data/config/state.json` 恢复原始默认路由与 metric，成功后删除 state 文件。原始值只在本轮**首次** apply 时记录，之后重复 apply 不会把它覆盖成被改过的值。

**Q11b：清理会不会误删我自己加的路由？**
不会。清理按“签名”识别自己的产物：nft 表名、主表中 `proto 200` 的明细路由（且出口网卡匹配）、`pref 20000+` 且表号在派生区间的 `ip rule`。内核直连路由（`proto kernel`）和你手写的静态路由（`proto static`）都不会被动（netns 集成用例覆盖）。

**Q12：只插了一张无线（有线没插网线），能关掉它吗？**
不能。**仅剩一张生效网卡（唯一承载默认路由）时禁止关闭**，避免主机断网。确需关闭用 `--force`（应急）。

**Q13：关闭网卡后断网了怎么恢复？**
`scripts/cli-netswitch.sh iface up <网卡>`；若连不上机器，物理重启后 DHCP 会自动恢复。

## 分流效果排查

**Q14：容器流量没走指定网卡？**
按顺序排查：
1. `sysctl net.ipv4.ip_forward` 应为 `1`；
2. `sysctl net.ipv4.conf.all.rp_filter` 应为 `0` 或 `2`（严格模式会丢跨网卡转发包）；
3. 容器网络若有 `-o <网卡> -j MASQUERADE` 显式规则，需覆盖目标出口网卡；
4. 用 `ip route get <目标IP> from <容器IP> iif <网桥名>` 看路由走向。

**Q15：GitHub 新增网段后分流漏了？**
CIDR 缓存默认 24h 自动刷新；紧急时在 `rules[].cidrs.extra` 手动补充网段，再重新应用规则。

**Q23：设置分流规则时报 `RTNETLINK answers: Operation not supported`？**
说明**本机内核/命名空间不支持策略路由（多路由表）**。常见原因：内核未启用 `CONFIG_IP_MULTIPLE_TABLES`（常见于精简/嵌入式内核），或运行在受限容器 / gVisor 中（容器内无法改路由规则）。
- 用 `scripts/preflight.sh` →「策略路由能力」确认（会临时增删一条测试路由并立即删除；并发预检时不会误报）。
- 程序会**自动回退 `mainroute` 后端**：在主路由表按目标网段加明细路由（`ip route replace <cidr> via <网关> dev <网卡>`），不需多路由表与 `ip rule`。
- 若 `preflight.sh` 里「主表路由」也失败（受限容器/gVisor），则确实无法分流；此时若只想让某张网卡承载**全部**流量，可改用「设为主网卡」（`m` / `metric primary`）。

**Q16：怎么确认分流是否生效？**
`scripts/cli-netswitch.sh status`；或在容器内访问规则目标站点，看去程公网 IP 是否为指定网卡。TUI 规则区会显示「生效网卡」与「已应用/未应用」。

## TUI

**Q17：TUI 按键没反应 / 显示"未绑定按键"？**
用 `↑`/`↓` 导航后按：网卡 `o`/`m`/`t`，规则 `e`/`p`/`c`。其它字母会提示"未绑定按键"。`Enter` 对**网卡**执行默认动作（设为主网卡），对规则仅提示。

**Q18：TUI 界面好像不刷新？**
界面每 ~1.5s 自动刷新，状态栏会显示「最后刷新 时间」。也可按 `f` 或 `F5` 手动刷新；`Esc` 返回主页面并刷新。

**Q19：TUI 里怎么退出？输入框里退不出？**
`q` 或 `Ctrl+C` 退出程序；输入/确认提示下 `Esc` 取消当前输入、`Ctrl+C` 退出。

**Q20：网卡颜色代表什么？**
主网卡（metric 最小）**绿色**；其它可用网卡**蓝色**；被禁止/不可用（管理关闭、未插网线）**红色**。

## 日志与自恢复

**Q21：日志在哪？**
默认 `logs/netswitch.log`（按大小轮转）。如果 `logs/` 被 root 创建而你现在是普通用户，程序会**自动改写到** `$XDG_STATE_HOME/netswitch/logs/netswitch.log`（并在 stderr 提示一句）；直接跑 `scripts/cli-netswitch.sh status` 可以看到“日志:”一行给出的实际路径。也可用 `NETSWITCH_LOG_DIR` 显式指定目录。默认记录写操作与高层动作；`NETSWITCH_LOG_LEVEL=DEBUG` 可含只读查询。

**Q22：怎么开机自动应用配置？**
`sudo scripts/cli-netswitch.sh install-systemd`（或 `sudo scripts/install.sh --with-systemd`）。服务开机联网后执行 `apply`（幂等）。

**Q24：我用普通用户登录、用 `sudo` 运行，会有问题吗？**
`sudo` 运行**没问题**（`euid=0`，权限检查通过；`SUDO_USER` 会显示你的登录名）。两点注意：
- **写操作必须 sudo**（非 root 运行 TUI/CLI 会明确提示；TUI 标题会显示「⚠ 非root·只读」）。
- 用 sudo 跑过后，`data/config/*`、`logs/*` 会变成 **root 所有**；日志写不进去时程序会自动回退到用户可写目录（不会静默丢失），但 `data/config/config.json` 仍需 root 才能写回（`detect --write` / TUI 改配置）。建议**统一用 sudo**，或 `sudo chown -R "$USER" data logs` 修回所有权。

## IPv6（双栈）

**Q25：为什么 IPv6 流量没走指定网卡？**
本程序默认**只分流 IPv4**（`routing.ip_versions` 默认 `["v4"]`，为了不改变升级前的行为）。开启方式与限制见 [user-manuals.md](user-manuals.md)（§3.5 开启 IPv6 分流）。常见原因：
- **没开双栈**：配置里加 `"routing": { "ip_versions": ["v4", "v6"] }` 后重新 `apply`；
- 出口网卡没有 v6 默认路由（`ip -6 route show default`），或只有 `fe80::` 链路本地地址（不能作为分流源地址）；
- 内核缺 `CONFIG_IPV6_MULTIPLE_TABLES`（仅 nftables/iprule 后端需要；`mainroute` 后端不需要）；
- 规则的 CIDR 源里本来就没有 v6 网段（`status` 中规则行的“族”列能看出该规则参与哪些族）。

以上环境不具备的情况都只是**告警并跳过 v6**，IPv4 分流不受影响；告警会出现在 `logs/netswitch.log` 与 TUI/CLI 的提示行。

**Q26：能不能只让某一条规则走 IPv6？**
可以。在该规则的 `cidrs` 里加 `"ip_versions": ["v6"]`（或 `["v4","v6"]`），它会覆盖全局 `routing.ip_versions`。若该规则只声明 `v6` 而环境没有 v6，这条规则会被整体跳过并告警，其它规则不受影响。

## 规则的新增/删除与域名

**Q27：在菜单里加了规则，为什么没生效？**
这是**有意设计**：TUI 的 `n`（新增）/ `x`（删除）与 CLI 的 `rule add` / `rule remove` **只写配置文件**，不改路由、不动网卡。要让它们作用到物理网卡，需要再执行一次：

- TUI：`a`（应用全部），或在选中该规则后按 `p`（应用）；
- CLI：`sudo scripts/cli-netswitch.sh apply`。

这样做的原因：先攒好配置、检查无误再一次性实施，避免边输入边改路由。反过来说，`e`（选出口）、`c`（撤销分流）、`p`、`a`、`r` 这些操作会**立即作用到网络**。

**Q28：`*.github.com` 能覆盖 github.com 的所有子域吗？**
**不能**。域名规则的工作方式是：在 **apply 时**把域名解析成 IP（A/AAAA），再按网段分流；而 DNS 没有“枚举某域下所有子域”的接口，所以：

- `*.github.com` 会解析 apex（`github.com`）本身，并做一次 **DNS 通配符探测**（对随机子域查询）。只有该域真的配置了 `*` 记录，才会额外拿到地址；
- 想精确覆盖就**把子域写出来**：`rule add '*.github.com' api.github.com gist.github.com codeload.github.com`；
- 解析结果有 TTL 缓存（默认 24h，`cidrs.ttl_hours` 可调），DNS 解析失败时会回退缓存并告警；
- 想彻底“跟随真实解析结果”需要 DNS 代理（dnsmasq/unbound + nftset）方案，本项目未采用（见 `docs/review-findings.md` 备选方案）。

**Q29：域名规则会不会拖慢 apply？**
会有一点点：每条规则每个未过期的域名会做一次 DNS 查询（`*.` 通配符多一次探测查询）。上限是每条规则 64 个域名；结果缓存内不再查询。若不想让程序发探测查询，可设 `"wildcard_probe": false`。
