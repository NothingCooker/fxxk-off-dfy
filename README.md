# fxxk-off-dfy —— B站「大肥鱼」机器人评论清理器（服务端版）

`https://github.com/NothingCooker/fxxk-off-dfy`

> 油猴脚本让你自己看不见机器人，**这个脚本让所有人看不见**。
>
> 走 B 站官方接口轮询你自己视频的评论 → 规则判定 → 直接删除（可选举报 / 拉黑）。
> 删除发生在服务端，刷新、换设备、匿名访问都看不到，评论区和互动数据真正干净。

参考项目：[bilibili-dafeiyu-blocker](https://github.com/ybyz2/bilibili-dafeiyu-blocker)（前端屏蔽）、
[Creel 鱼篓](https://github.com/cycocyco/Creel)（扩展层拦截）。
它们的规则库很准，但都只在**你的浏览器里**生效——机器人评论还挂在你的稿件下，别人照样看到、照样被引流。
本项目复用它们的判定思路（昵称「鱼/鲸」、品牌口头禅、Prompt 残留、数值矩阵、洗稿重复），
把处置动作换成 API 删除。

---

## 目录

- [原理](#原理)
- [5 分钟上手](#5-分钟上手)
- [Cookie 怎么拿](#cookie-怎么拿)
- [规则档位与误杀](#规则档位与误杀)
- [配置项](#配置项)
- [命令](#命令)
- [举报理由](#举报理由)
- [风控与节奏](#风控与节奏)
- [后台常驻](#后台常驻)
- [日志与审计](#日志与审计)
- [FAQ](#faq)

---

## 原理

```
登录态 Cookie
      │
      ▼
① 发现目标稿件        config.videos 手填 bvid，或 space/wbi/arc/search 自动拉自己的投稿
      │
      ▼
② 拉评论（增量）      x/v2/reply（sort=0 时间倒序，逐页）
      │                ↓ 命中风控时自动切 x/v2/reply/wbi/main（WBI 签名）
      ▼
③ 规则判定           昵称/品牌词/Prompt 残留/数值矩阵/重复文本 → 硬命中
      │               @、总结模板、长文、全角标点、低等级 → 软信号累加
      ▼
④ 处置               report（可选，先举报）→ del（删除）→ block（可选，反复命中拉黑）
      │
      ▼
⑤ 落盘               state.json 记录已处理 rpid，events.jsonl 逐条审计
```

几个关键设计：

| 设计 | 原因 |
|---|---|
| 先举报、后删除 | 评论删掉后举报入口就没了；举报和删除是两条独立的打击线 |
| 默认 `dry_run=true` | 第一次上线不删任何东西，只把「本来会删什么」打出来，人工确认规则 |
| `state.json` 记 rpid | 重启不重复删、不重复举报；B站对重复操作会记异常 |
| 置顶评论、UP 主自己的评论默认跳过 | 你置顶的说明 / 自己补的评论不该被自己的脚本干掉 |
| 同屏重复文本聚类 | 洗稿矩阵的典型特征：一屏里三个不同账号发一模一样的 30 字小作文 |
| 楼中楼单独扫 | 机器人最爱在楼中楼 `@` 人拉互动，只扫主楼会漏 |

### 和参考项目的区别

| | bilibili-dafeiyu-blocker | Creel 鱼篓 | fxxk-off-dfy（本项目） |
|---|---|---|---|
| 生效范围 | 只有你本机浏览器 | 只有你本机浏览器 | 服务端，所有访客 |
| 机器人的评论还在吗 | 在，别人看得到 | 在 | **被删除** |
| 会不会影响你的推荐/互动数据 | 不会，脏数据照样算 | 不会 | 不会，直接清掉 |
| 需要装扩展 | 是 | 是 | 不需要，Python 跑在电脑或服务器上 |
| 能否举报 / 拉黑 | 否 | 否 | 可选开启 |
| 误杀的影响 | 本地看不见，无痕 | 本地看不见 | **真删了，找不回来** |

最后一行是重点：本项目的处置不可逆。所以默认 dry-run，先跑 24 小时看日志再开删。

---

## 5 分钟上手

### 0. 环境

Python 3.9+，**零第三方依赖**（标准库实现，内网/服务器都能跑）。

```bash
python --version
```

### 1. 生成配置

```bash
python fxxk_off_dfy.py init
```

生成 `config.json`，里面有 `cookie`、`mid`、`videos` 三个字段要你填。

### 2. 填 Cookie

见下一节。填完先验一下：

```bash
python fxxk_off_dfy.py whoami
```

正常输出你的昵称、mid、最近投稿列表。报 `code=-101` 就是 Cookie 没抄全或过期了。

### 3. 体检（只打印，不删除）

```bash
python fxxk_off_dfy.py check
```

输出类似：

```
[DRY] 命中 name:/鱼/ | score=100 | [1931...] 旅行者的大肥鱼: 这期视频总结得真好，学到了
```

一条都不命中也很正常——说明你的评论区目前干净。想让脚本对历史评论也生效，
把 `lookback_hours` 调大（比如 `720`）再跑一次 `check`。

### 4. 正式开删

确认 `check` 的结果没有误杀后：

```bash
# 方式 A：改 config.json 的 dry_run 为 false，然后
python fxxk_off_dfy.py run

# 方式 B：命令行强制 live，只跑一轮
python fxxk_off_dfy.py run --once --live

# 想连举报一起做
python fxxk_off_dfy.py run --live --report
```

`run` 会一直循环（默认 120 秒一轮，带随机抖动）。Ctrl+C 退出。

---

## Cookie 怎么拿

1. Chrome / Edge 打开 `https://www.bilibili.com`，确保**已登录**。
2. F12 → Network（网络）→ 刷新页面 → 点任意一个 `api.bilibili.com` 请求。
3. Request Headers 里找到 `Cookie:`，整行复制（很长，全部要）。
4. 粘进 `config.json` 的 `cookie` 字段。

必须包含的项：

| 字段 | 用途 |
|---|---|
| `SESSDATA` | 登录态 |
| `bili_jct` | CSRF token，删除/举报/拉黑接口必须要 |
| `DedeUserID` | 你的 mid（脚本也会从 nav 接口自动取） |

建议一并带上的项（缺了更容易吃 `-352` 风控）：`buvid3`、`buvid4`、`b_nut`、`bili_ticket`、`_uuid`。

> Cookie 等价于账号密码。别提交到 git、别发群里、别贴 issue。
> 本项目 `.gitignore` 已经排除 `config.json`、`state.json`、`events.jsonl`。

---

## 规则档位与误杀

三档，`config.json` 的 `tier`：

| 档位 | 硬命中（所有档位一致） | 软信号 | 误杀风险 |
|---|---|---|---|
| `cup`（小杯） | 昵称含鱼/鲸、品牌词、Prompt 残留、数值矩阵、三个以上账号同文 | 不参与打分 | 极低，只删铁证 |
| `standard`（默认） | 同上 | + 软词（主人/喵…）、`@`、总结模板、超长文 | 低 |
| `kill`（杀戮） | 同上 | + 全角标点、破折号、直角引号、正文单字「鱼/鲸」 | 明显上升，真人长评也可能中招 |

默认 `threshold = 60`。软信号要攒到 60 才判定，而且**每条软规则自身都够不到 60**：

| 软信号 | 分值 | 说明 |
|---|---|---|
| `at` | 20 | 正文（剥掉「回复 @xx :」前缀后）出现 @ |
| `summary` | 40（封顶） | 命中 ≥2 个总结模板词，再多也只有 40 |
| `long` | 15 | 正文 ≥300 字 |
| `char` | 30 | 正文出现单字「鱼/鲸」（仅 `kill` 档） |
| 全角标点 / 破折号 / 直角引号 | 各 15 | 仅 `kill` 档，可叠加 |
| `ai_name` | 25 | 昵称带 AI/Bot/GPT/助手 |
| `low_level` | 10 | Lv0~1 且正文 ≥30 字 |
| `soft_words` | 22~40 | 主人/喵/本喵…，按词长降序匹配，不重复计分 |

所以「真人 @ 一下」只有 20 分、「真人写了段总结」只有 40 分，都不会被删；
至少要「@ + 总结」或「@ + 单字鱼 + 标点」这种组合才会过线。

想看每条为什么命中，看日志里的 `rules` 字段：

```
name:/鱼/              昵称命中（最高精度，两套参考方案都标 100%）
brand:本鲸             正文出现品牌词
prompt:语言模型         Prompt / System Prompt 泄漏
numeric:3项(96.5%...)  一条评论里 3 个以上精确百分比/倍数，洗稿总结特征
dup_matrix:3个不同账号同文   同屏三个账号发一样的长文本
at+20 / summary+40 / long+15 / char+30 ...
```

**想加自己的规则**：

```jsonc
"custom_words": ["某某AI", "关注我领资料"],   // 命中即删，不管档位、不看分数，正文或昵称都算
"name_patterns": ["鱼", "鲸", "某某家的"],    // 正则，只对昵称生效
"brand_words": ["大肥鱼", "本鲸"],            // 只对正文生效
"whitelist_mids": [123456],                  // 永不处理这些账号
"whitelist_rpids": [987654321],              // 永不处理这些评论
```

**被误删了怎么办**：`events.jsonl` 里有原始 `message` 和 `rpid`，翻出来人工恢复；
然后把这个特征加进 `whitelist_mids` 或把 `tier` 降回 `cup`。

---

## 配置项

`config.json`（完整字段见 `config.example.json`）：

| 字段 | 默认 | 说明 |
|---|---|---|
| `cookie` | `""` | 登录态 Cookie，**必填** |
| `mid` | `0` | 你的 UID，留 0 会用登录账号自动填 |
| `videos` | `[]` | 手动指定要监控的 bvid（`BV...`）或 aid 列表，如 `["BV1xx411c7mD"]` |
| `auto_discover` | `true` | 自动拉自己最近投稿（`discover_pages` 页，每页 30） |
| `interval_seconds` | `120` | 轮询间隔（有 ±10% 随机抖动） |
| `lookback_hours` | `72` | 只处理这个时间范围内的评论，**超出窗口的评论不会被删**；想清历史调到 `720` |
| `max_pages` | `8` | 每个视频每轮最多翻几页主楼（每页 20 条 = 160 条） |
| `page_size` | `20` | 每页条数，接口上限就是 20，写大了会自动夹到 20 |
| `include_sub_replies` | `true` | 是否扫楼中楼 |
| `sub_reply_pages` | `2` | 每条主楼最多看几页楼中楼（取**最后**几页，因为楼中楼按时间正序给） |
| `wbi_mode` | `2` | `wbi/main` 备用通道的排序模式，`2`=按时间（默认）；排序不确定时脚本不会推进游标 |
| `dry_run` | `true` | 只记录不删除。**确认无误会后再改 false**（写成 `null`/`""`/`0` 也一律按 dry-run 处理） |
| `report` | `false` | 命中后一并举报（先举报再删除） |
| `report_reason` | `8` | 举报理由，见下节 |
| `auto_block` | `false` | 同一账号累计命中 `block_after_hits` 次后拉黑 |
| `block_after_hits` | `3` | 拉黑阈值 |
| `skip_pinned` | `true` | 跳过置顶评论 |
| `skip_self` | `true` | 跳过 UP 主自己的评论 |
| `threshold` | `60` | 软信号阈值（只有软信号会累加，单条软信号最多 40 分，永远不够） |
| `tier` | `"standard"` | `cup` / `standard` / `kill`，写错会直接报错退出，不会静默降级 |
| `numeric_matrix_min` | `3` | 一条评论里几个精确百分比/倍数算「洗稿矩阵」 |
| `summary_min_hits` | `2` | 命中几个总结模板词开始计分 |
| `long_text_len` | `300` | 超长评论阈值（加 15 分） |
| `duplicate_min_len` | `12` | 重复文本聚类的最短长度 |
| `request_min_delay` / `request_max_delay` | `0.8` / `1.6` | 请求间隔（秒），别调到 0 |
| `timeout` | `20` | 单请求超时（秒） |
| `max_retries` | `3` | 失败后的**额外**重试次数（总尝试 = 1 + 3） |
| `state_file` / `log_file` | `state.json` / `events.jsonl` | 状态与审计日志路径；相对路径按配置文件所在目录解析 |

---

## 命令

```bash
python fxxk_off_dfy.py init       [--cookie "..."] [--force]
python fxxk_off_dfy.py whoami
python fxxk_off_dfy.py check      [--video BV1xx BV2yy] [--tier cup] [--threshold 80]
python fxxk_off_dfy.py run        [--once] [--live] [--report] [--block]
                               [--interval 120] [--tier kill] [--threshold 50] [--video BV1xx]
python fxxk_off_dfy.py stats
python fxxk_off_dfy.py selftest    # 离线规则自测，不联网、不要 Cookie
```

`selftest` 会跑 16 个正负样本 + wbi 签名校验，改完规则先跑它：

```
PASS  昵称带鱼 -> 硬命中          expect=True  got=True  score=100 name:/鱼/
PASS  真人聊鱼 -> 不命中          expect=False got=False score=0   -
PASS  真人 @ -> 不命中            expect=False got=False score=20  at+20
...
17 个用例，失败 0 个。
```

还有一组离线集成测试（假接口，验证「拉评论 → 判定 → 处置 → 落盘」全流程）：

```bash
python tests/test_offline.py
```

覆盖：dry-run 不删、live 删除后二轮跳过、楼中楼扫描、评论已消失、先举报后删除、
反复命中拉黑、置顶与白名单。改完代码先跑这两个，都不用 Cookie。

---

## 举报理由

`x/v2/reply/report` 的 `reason` 是整数枚举，脚本内置映射：

```python
1  垃圾广告      2  色情低俗     3  人身攻击    4  引战
5  剧透          6  政治敏感     7  违法违禁    8  垃圾信息/刷屏   ← 默认
```

机器人评论用 `8` 最贴切。这个枚举是社区整理值，B 站改过几次；
如果举报接口返回参数错误（`code=-400`），换成 `1` 再试一条，用 `events.jsonl` 里
`action=report_failed` 记录的 `code` 判断。举报失败不会影响删除。

举报成功率取决于你的理由是否成立，别指望必中。**删除是主手段，举报是补刀。**

---

## 风控与节奏

B 站的接口风控主要看：Cookie 是否完整、请求频率、UA、Referer、IP 信誉。

脚本已经处理：

- 每次请求随机间隔（默认 0.8~1.6 秒）
- `-352 / -412 / -509 / -799` 和 HTTP 412/429 → 指数退避重试（8s → 16s → 32s，封顶 120s）
- 主楼接口被风控 → 自动切 `wbi/main` 备用通道（带 WBI 签名）
- 错误日志带 `code`，方便判断是被限流还是接口改版

建议：

- `interval_seconds` 不要低于 60；视频多的话调大 `max_pages` 前先降低频率
- 别拿小号 Cookie 扫几百个视频，风控值跟账号绑定
- 服务器跑的话，最好用你平时登录 B 站的地区/网络，机房 IP 更容易吃风控
- 第一次上线跑 `run --once` 观察一天，再上常驻

---

## 后台常驻

Windows 计划任务（每 5 分钟跑一轮，不需要常开窗口）：

```powershell
# 管理员 PowerShell 里执行
$py = (Get-Command python).Source
$dir = "D:\path\to\fxxk-off-dfy"
$act = New-ScheduledTaskAction -Execute $py -Argument "`"$dir\fxxk_off_dfy.py`" run --once --live" -WorkingDirectory $dir
$trg = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "fxxk-off-dfy" -Action $act -Trigger $trg -Description "B站大肥鱼评论清理"
```

Linux / 服务器（systemd 或直接 nohup）：

```bash
nohup python3 fxxk_off_dfy.py run --live >> run.log 2>&1 &
```

开机自启、崩溃重启用 systemd：

```ini
[Unit]
Description=fxxk-off-dfy
After=network-online.target

[Service]
WorkingDirectory=/opt/fxxk-off-dfy
ExecStart=/usr/bin/python3 fxxk_off_dfy.py run --live
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

---

## 日志与审计

**`events.jsonl`**（一行一条，UTF-8）：

```json
{"ts": 1730000000, "time": "2025-10-27 12:13:20", "action": "deleted", "aid": 113000000000000,
 "rpid": 193100000000, "mid": 3546000000, "uname": "旅行者的大肥鱼",
 "message": "本鲸看完了，这期视频……", "rules": ["name:/鱼/"], "score": 100, "reported": true}
```

`action` 取值：

| action | 含义 |
|---|---|
| `would_delete` | dry-run 命中，实际没删 |
| `deleted` | 已删除 |
| `already_gone` | 评论已经没了（作者删了/已被系统处理），记入 handled 不再重试 |
| `delete_failed` | 删除失败，带 `code`/`error`；下轮会重试，连续 3 次才放弃 |
| `report_failed` | 举报失败，带 `code`/`error`（不影响删除） |
| `blocked` | 已拉黑账号 |

**`state.json`**：每个视频处理到哪条、已拉黑名单、累计统计。想重新全量扫描，删掉它即可
（注意：删了会重复举报已经处理过的评论，风控上不划算）。

---

## FAQ

**Q：会删到真人吗？**
默认档位只对「昵称含鱼/鲸」「品牌词」「Prompt 残留」「数值矩阵」这些铁证下死手，
真人聊天里的「鱼」不会被删（`standard` 档不启用单字判定）。但还是那句话：先 `check` 跑一天。

**Q：能用在别人的视频下吗？**
不能。B 站只允许稿件 UP 主删除自己稿件下的评论，接口层面就会拒绝；
本项目也只用登录账号管理自己的稿件。

**Q：删掉的评论能恢复吗？**
不能。UP 主删除是硬删，没有回收站。`events.jsonl` 里的原文是唯一的记录。

**Q：为什么 `check` 一条都没命中？**
可能是评论区本来就干净，也可能是 `lookback_hours=72` 把历史评论排除了。
调大到 `720` 再看；或者确认 `auto_discover` 拉到了你的稿件（`whoami` 会打印最近投稿）。

**Q：评论区很热闹，一轮翻不完怎么办？**
脚本从最新评论往回翻，翻满 `max_pages`（默认 8 页 = 160 条）还没追到上次处理位置时，
日志会提示「翻满 N 页仍未追平」，此时**不会**推进游标，下一轮重来。
把 `max_pages` 调到 20~30，或者把 `interval_seconds` 调小，直到日志不再出现这条提示。

**Q：评论明明很多，脚本却一条都没扫到？**
B 站老接口 `x/v2/reply` 在未登录或被风控时会返回 `code=0` + 空列表（不是报错）。
脚本检测到「第一页空」会自动切到前端在用的 `x/v2/reply/wbi/main`（带 WBI 签名）再试一次，
日志里会出现「老接口返回空评论，已切换 wbi/main 通道」。
两个通道都空，才说明评论区真的没有评论，或者 Cookie 已经失效（先跑 `whoami`）。

**Q：`-352` 一直不退怎么办？**
换回浏览器复制的新 Cookie（带上 `bili_ticket`、`buvid3`），把 `interval_seconds` 调到 300 以上，
停一两个小时再跑。机房 IP 建议挂在你日常网络的出口。

**Q：B 站改接口了怎么办？**
`whoami` 和 `check` 会直接报出 `code`。本项目只用两个老接口 + 一个 WBI 备用通道，
真改版的话改 `BiliAPI` 里的 URL 和字段映射即可，规则引擎不用动。

---

## License

MIT
