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
`table_id`/`fwmark` 不填会自动分配且保证唯一；`interface` 也可以后续在 TUI 里用 `e` 选择。

**Q8：默认 `rules` 是空的？**
是。默认配置**不写死任何规则**（github 仅作文档示例），按需自行添加。

## 操作

**Q9：怎么把某张网卡设为主网卡？**
TUI：选中网卡按 `m`（或 `Enter`）→ `y` 确认。CLI：`sudo scripts/cli-netswitch.sh metric primary <网卡名>`。原理是把该网卡 metric 设为最小（默认 100，其余 600）。

**Q10：怎么撤销某条分流规则？**
TUI：选中规则按 `c`；CLI：`sudo scripts/cli-netswitch.sh rule clear <规则名>`。两者都会清空该规则在 `config.json` 里的 `interface`（**持久撤销**），下次 `apply` 不会复活。

**Q11：怎么完全恢复原状？**
`sudo scripts/cli-netswitch.sh revert`：清理策略路由并按 `data/config/state.json` 恢复原始默认路由与 metric。

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
`logs/netswitch.log`（按大小轮转）。默认记录写操作与高层动作；`NETSWITCH_LOG_LEVEL=DEBUG` 可含只读查询；`NETSWITCH_LOG_DIR` 可改目录。

**Q22：怎么开机自动应用配置？**
`sudo scripts/cli-netswitch.sh install-systemd`（或 `sudo scripts/install.sh --with-systemd`）。服务开机联网后执行 `apply`（幂等）。
