#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fxxk_off_dfy.py —— B站「大肥鱼 / AI 机器人」评论清理器（服务端 API 版）

和油猴脚本的区别：
    油猴（bilibili-dafeiyu-blocker）/ 扩展（Creel）只是「让你自己看不见」，机器人的评论
    还挂在你的视频下面，别人照样看得到，数据、推荐、互动全被吃掉。
    这个脚本走 B 站官方 API：轮询你自己视频的评论 → 规则判定 → 直接删除（可选举报、拉黑）。
    删掉的是真评论，所有访客都看不到了，这才叫从根本解决。

用法：
    python fxxk_off_dfy.py init            # 生成 config.json（可粘 Cookie）
    python fxxk_off_dfy.py whoami          # 校验 Cookie、打印账号信息
    python fxxk_off_dfy.py check           # 只看不删（dry-run 体检，强烈建议先跑）
    python fxxk_off_dfy.py run             # 守护循环，按 interval_seconds 轮询
    python fxxk_off_dfy.py run --once      # 跑一轮就退出（给计划任务用）
    python fxxk_off_dfy.py selftest        # 离线跑规则引擎自测，不需要 Cookie

依赖：仅标准库（requests 可选，没有就走 urllib）。Python 3.9+。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.0.0"

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.json")
DEFAULT_STATE = os.path.join(HERE, "state.json")
DEFAULT_LOG = os.path.join(HERE, "events.jsonl")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# B 站评论举报理由枚举。接口只认整数，前端文案大致如下；
# 若举报返回 -400 / 参数错误，把 config.report_reason 换成别的值试。
# ---------------------------------------------------------------------------
REPORT_REASONS = {
    1: "垃圾广告",
    2: "色情低俗",
    3: "人身攻击",
    4: "引战",
    5: "剧透",
    6: "政治敏感",
    7: "违法违禁",
    8: "垃圾信息/刷屏",
}
DEFAULT_REPORT_REASON = 8

# ---------------------------------------------------------------------------
# 默认规则表（从 bilibili-dafeiyu-blocker 与 Creel 的规则库整理，并按「服务端删除」
# 这个更狠的场景调整了保守程度：宁可漏一条，不要删掉真人）。
# ---------------------------------------------------------------------------

# 昵称硬命中：一定是「某某的大肥鱼」这类模板。注意不能只写「鱼」——
# 实测真评论区里就有「章鱼幻想」「鲨鱼风暴辣椒」「鱼丙丙」这种昵称。
DEFAULT_NAME_PATTERNS = [r"大肥鱼", r"肥鱼", r"鲸鱼", r"大鲸", r"鲛鱼", r"家的DSH"]
# 昵称里出现单个鱼/鲸只加分（40），必须配合正文特征才判死
DEFAULT_NAME_SOFT = [r"鱼", r"鲸"]
DEFAULT_BRAND_WORDS = [
    "大肥鱼", "肥鱼", "鲸鱼", "大肥鲸", "鲸鱼娘",
    "本鲸", "本鱼", "本肥鱼", "小鲛鱼",
]
DEFAULT_PROMPT_WORDS = [
    "作为AI", "作为 AI", "作为人工智能", "语言模型", "人工智能助手",
    "系统提示词", "我的设定", "被设定为", "prompt里", "prompt 里",
    "我是被派来", "主人拿自己的活",
]

# 软命中：累加打分，总分 >= threshold 才判定。
DEFAULT_SOFT_WORDS = {
    "主人": 30,      # 宠物区真人也会说「主人」，单独不给硬命中
    "喵": 22,        # 猫娘口癖，机器人高发
    "喵~": 25,
    "本喵": 40,
    "家人们": 8,
}

# 洗稿总结模板词：同一条里出现 >= summary_min_hits 个就给 summary_weight 分。
DEFAULT_SUMMARY_WORDS = [
    "首先", "其次", "再次", "最后", "综上", "总之", "总结一下",
    "整体来看", "总的来说", "视频中提到", "up主提到", "UP主提到",
    "值得注意的是", "需要指出",
]

# 弱信号（杀戮档 / 提高灵敏度时才有意义）
DEFAULT_AI_NAME_SUFFIX = [r"AI", r"Bot", r"GPT", r"助手", r"智能", r"数字人"]

DEFAULT_CONFIG_DATA: Dict[str, Any] = {
    "_comment": "把 Cookie 填进 cookie 字段（含 SESSDATA 和 bili_jct），然后先跑 check 再跑 run。",

    "cookie": "",
    "user_agent": USER_AGENT,
    "mid": 0,
    "videos": [],
    "auto_discover": True,
    "discover_pages": 2,

    "interval_seconds": 120,
    "lookback_hours": 72,
    "full_history": False,
    "max_pages": 8,
    "page_size": 20,
    "include_sub_replies": True,
    "sub_reply_pages": 2,
    "wbi_mode": 2,

    "dry_run": True,
    "report": False,
    "report_reason": DEFAULT_REPORT_REASON,
    "auto_block": False,
    "block_after_hits": 3,

    "skip_pinned": True,
    "skip_self": True,
    "whitelist_mids": [],
    "whitelist_rpids": [],

    "threshold": 60,
    "tier": "standard",
    "name_patterns": DEFAULT_NAME_PATTERNS,
    "name_soft_patterns": DEFAULT_NAME_SOFT,
    "brand_words": DEFAULT_BRAND_WORDS,
    "prompt_words": DEFAULT_PROMPT_WORDS,
    "soft_words": DEFAULT_SOFT_WORDS,
    "summary_words": DEFAULT_SUMMARY_WORDS,
    "ai_name_suffix": DEFAULT_AI_NAME_SUFFIX,
    "custom_words": [],

    "numeric_matrix_min": 3,
    "summary_min_hits": 2,
    "long_text_len": 300,
    "duplicate_min_len": 12,

    "request_min_delay": 0.8,
    "request_max_delay": 1.6,
    "timeout": 20,
    "max_retries": 3,

    "state_file": "state.json",
    "log_file": "events.jsonl",
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def now_ts() -> int:
    return int(time.time())


def fmt_time(ts: Optional[int]) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")


def jitter_sleep(lo: float, hi: float) -> None:
    lo, hi = max(0.0, lo), max(lo, hi)
    time.sleep(random.uniform(lo, hi))


# 日志出口：GUI 会注入一个回调，把日志同步到窗口里
LOG_SINK: Optional[Any] = None
_LOG_QUIET = False


def set_log_sink(fn: Optional[Any], quiet: bool = True) -> None:
    """给 GUI 用：fn(level, text)，level 取 'info' / 'warn'。quiet=True 时不再打印到控制台。"""
    global LOG_SINK, _LOG_QUIET
    LOG_SINK = fn
    _LOG_QUIET = bool(quiet)


def _emit(level: str, text: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    line = "%s  %s" % (stamp, text) if level == "info" else "%s  [!] %s" % (stamp, text)
    if not _LOG_QUIET:
        try:
            print(line, flush=True)
        except Exception:
            pass
    sink = LOG_SINK
    if sink is not None:
        try:
            sink(level, line)
        except Exception:
            pass


def log(msg: str) -> None:
    _emit("info", msg)


def warn(msg: str) -> None:
    _emit("warn", msg)


def truncate(s: str, n: int = 48) -> str:
    s = s.replace("\r", " ").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def normalize_text(s: str) -> str:
    """归一化，用于重复文本聚类和关键词匹配（全角->半角、去空白、小写）。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", "", s)
    return s.lower()


def load_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        raise SystemExit("配置文件不是合法 JSON：%s (%s)" % (path, e))


def as_list(value: Any) -> List[Any]:
    """配置项容错：允许写成字符串、列表或空值。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


def is_live_flag(value: Any) -> bool:
    """只有明确的 false / "false" / "live" 才算「真的删」。
    null、空串、缺省、类型写错一律按 dry-run 处理 —— 宁可少删，不能误删。"""
    if value is False:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("false", "live")
    return False


def save_json(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(tmp, 0o600)   # config.json 里是 Cookie；Windows 上 no-op
    except OSError:
        pass
    os.replace(tmp, path)


def merge_config(user: Dict[str, Any]) -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG_DATA))  # deep copy
    cfg.update({k: v for k, v in (user or {}).items() if not k.startswith("_")})
    # 旧版把「鱼」「鲸」当成昵称硬命中，实测会误删「章鱼幻想」「鲨鱼风暴辣椒」这类真人昵称。
    # 老配置文件里如果还是旧值，自动升级成「昵称模板硬命中 + 单字只加分」。
    legacy = {str(x) for x in as_list(cfg.get("name_patterns"))}
    if legacy and legacy <= {"鱼", "鲸"}:
        cfg["name_patterns"] = list(DEFAULT_NAME_PATTERNS)
        warn("检测到旧版昵称规则（单字「鱼/鲸」直接判死），已自动升级为只加分，避免误删真人。")
    if "name_soft_patterns" not in cfg or not as_list(cfg.get("name_soft_patterns")):
        cfg["name_soft_patterns"] = list(DEFAULT_NAME_SOFT)
    return cfg


# ---------------------------------------------------------------------------
# WBI 签名（B 站 2023 起对 space / reply/wbi 等接口强制要求）
# ---------------------------------------------------------------------------

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


def _mixin_key(img_key: str, sub_key: str) -> str:
    raw = (img_key + sub_key)
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB if i < len(raw))[:32]


def wbi_sign(params: Dict[str, Any], img_key: str, sub_key: str, wts: Optional[int] = None) -> Dict[str, Any]:
    """返回带 wts / w_rid 的新参数字典（原字典不改）。"""
    signed = {k: v for k, v in params.items()}
    signed["wts"] = int(wts if wts is not None else time.time())
    # 过滤 !'()* 这些字符，并按键名排序
    cleaned = {
        k: "".join(ch for ch in str(v) if ch not in "!'()*")
        for k, v in sorted(signed.items())
    }
    query = urllib.parse.urlencode(cleaned, quote_via=urllib.parse.quote)
    mixin = _mixin_key(img_key, sub_key)
    cleaned["w_rid"] = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
    return cleaned


def _key_from_url(url: str) -> str:
    return os.path.splitext(os.path.basename(url or ""))[0]


# ---------------------------------------------------------------------------
# HTTP 客户端：Cookie、重试、风控退避
# ---------------------------------------------------------------------------

class BiliError(Exception):
    def __init__(self, code: int, message: str, url: str = ""):
        super().__init__("code=%s %s" % (code, message))
        self.code = code
        self.message = message
        self.url = url


class RiskControlError(BiliError):
    """-352 / -412 / HTTP 412/429：被风控限流，需要退避。"""


class HttpClient:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.timeout = int(cfg.get("timeout") or 20)
        self.cookie = (cfg.get("cookie") or "").strip()
        self.ua = cfg.get("user_agent") or USER_AGENT
        self.max_retries = int(cfg.get("max_retries") or 3)
        self.min_delay = float(cfg.get("request_min_delay") or 0.8)
        self.max_delay = float(cfg.get("request_max_delay") or 1.6)
        self._ssl_ctx = ssl.create_default_context()
        self._img_key = ""
        self._sub_key = ""
        self._wbi_ts = 0.0
        self.auth_failed = False
        self.stats = {"requests": 0, "errors": 0}

    # -- cookie ------------------------------------------------------------
    def csrf(self) -> str:
        m = re.search(r"(?:^|;\s*)bili_jct=([^;]+)", self.cookie)
        if not m:
            # 抛业务异常而不是 SystemExit：调用点可能被 try/except 包住，
            # SystemExit 会直接终止整个守护进程
            raise BiliError(-403, "Cookie 缺少 bili_jct，删除/举报接口无法通过校验"
                                  "（重新复制完整 Cookie，必须含 bili_jct）")
        return m.group(1).strip()

    def sessdata(self) -> str:
        m = re.search(r"(?:^|;\s*)SESSDATA=([^;]+)", self.cookie)
        return m.group(1).strip() if m else ""

    def preflight(self) -> None:
        """启动预检：Cookie 必须同时具备 SESSDATA 和 bili_jct，否则直接退出并说清原因。"""
        if not self.cookie:
            raise SystemExit("config.json 的 cookie 是空的。先跑 python fxxk_off_dfy.py init，再把浏览器 Cookie 填进去。")
        if not self.sessdata():
            raise SystemExit("Cookie 里没有 SESSDATA（登录态），无法读取/删除评论。")
        try:
            self.csrf()
        except BiliError as e:
            raise SystemExit(str(e))

    def headers(self, referer: str = "https://www.bilibili.com/") -> Dict[str, str]:
        return {
            "User-Agent": self.ua,
            "Referer": referer,
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cookie": self.cookie,
        }

    def _backoff(self, attempt: int, base: float = 8.0) -> float:
        return min(120.0, base * (2 ** attempt)) + random.uniform(0, 3)

    # -- raw ---------------------------------------------------------------
    def _request(self, method: str, url: str, params: Optional[Dict[str, Any]] = None,
                 data: Optional[Dict[str, Any]] = None,
                 referer: str = "https://www.bilibili.com/") -> Dict[str, Any]:
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        body = None
        headers = self.headers(referer)
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"

        last_err: Optional[Exception] = None
        attempts = 1 + max(0, self.max_retries)   # max_retries = 失败后的额外重试次数
        for attempt in range(attempts):
            try:
                jitter_sleep(self.min_delay, self.max_delay)
                req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
                self.stats["requests"] += 1
                with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_ctx) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    if "<html" in raw[:200].lower():
                        raise RiskControlError(-412, "返回了风控页面（HTML）", url)
                    raise BiliError(-1, "返回不是 JSON：%s" % raw[:80], url)
                code = int(payload.get("code", 0))
                if code == 0:
                    return payload
                if code in (-352, -412, -509, -799):
                    raise RiskControlError(code, payload.get("message") or "风控", url)
                if code == -101:
                    self.auth_failed = True
                    raise BiliError(code, "账号未登录，Cookie 失效或缺少 SESSDATA", url)
                raise BiliError(code, str(payload.get("message") or ""), url)
            except RiskControlError as e:
                last_err = e
                self.stats["errors"] += 1
                self._wbi_ts = 0.0   # WBI 密钥可能已轮换，下次重新拉
                backoff = self._backoff(attempt)
                warn("风控 %s，退避 %.0fs 后重试 (%d/%d)"
                     % (e.code, backoff, attempt + 1, attempts - 1))
                time.sleep(backoff)
            except BiliError:
                # 业务错误不重试：-404 已删除、-400 参数错误、-101 未登录…
                raise
            except urllib.error.HTTPError as e:
                last_err = e
                self.stats["errors"] += 1
                if e.code in (412, 429, 503):
                    backoff = self._backoff(attempt)
                    warn("HTTP %s，退避 %.0fs 后重试 (%d/%d)"
                         % (e.code, backoff, attempt + 1, attempts - 1))
                    time.sleep(backoff)
                    continue
                raise BiliError(e.code, "HTTP 错误：%s" % e.reason, url)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                self.stats["errors"] += 1
                backoff = 3 * (attempt + 1)
                warn("网络错误 %s，%ds 后重试 (%d/%d)" % (e, backoff, attempt + 1, attempts - 1))
                time.sleep(backoff)
        if isinstance(last_err, RiskControlError):
            raise last_err   # 保留原始类型，让上层能切通道/跳过
        raise BiliError(-1, "重试耗尽：%s" % last_err, url)

    def get(self, url: str, params: Optional[Dict[str, Any]] = None,
            referer: str = "https://www.bilibili.com/") -> Dict[str, Any]:
        return self._request("GET", url, params=params, referer=referer)

    def post(self, url: str, data: Dict[str, Any],
             referer: str = "https://www.bilibili.com/") -> Dict[str, Any]:
        return self._request("POST", url, data=data, referer=referer)

    # -- wbi ---------------------------------------------------------------
    def load_wbi_keys(self, force: bool = False) -> Tuple[str, str]:
        if not force and self._img_key and time.time() - self._wbi_ts < 3600:
            return self._img_key, self._sub_key
        payload = self.get("https://api.bilibili.com/x/web-interface/nav")
        data = payload.get("data") or {}
        if not data.get("isLogin"):
            # 不抛 SystemExit：调用链里可能被单个视频的 try/except 包住，
            # 抛 SystemExit 会直接干掉整个进程
            raise BiliError(-101, "未登录，无法取得 wbi_img（需要 SESSDATA）")
        img = _key_from_url((data.get("wbi_img") or {}).get("img_url", ""))
        sub = _key_from_url((data.get("wbi_img") or {}).get("sub_url", ""))
        if not img or not sub:
            raise BiliError(-1, "nav 没返回 wbi_img，接口可能改版了")
        self._img_key, self._sub_key, self._wbi_ts = img, sub, time.time()
        return img, sub

    def wbi_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        img, sub = self.load_wbi_keys()
        return wbi_sign(params, img, sub)


# ---------------------------------------------------------------------------
# B 站接口封装
# ---------------------------------------------------------------------------

API_NAV = "https://api.bilibili.com/x/web-interface/nav"
API_SPACE_ARC = "https://api.bilibili.com/x/space/wbi/arc/search"
API_VIEW = "https://api.bilibili.com/x/web-interface/view"
API_REPLY = "https://api.bilibili.com/x/v2/reply"
API_REPLY_WBI_MAIN = "https://api.bilibili.com/x/v2/reply/wbi/main"
API_REPLY_SUB = "https://api.bilibili.com/x/v2/reply/reply"
API_REPLY_DEL = "https://api.bilibili.com/x/v2/reply/del"
API_REPLY_REPORT = "https://api.bilibili.com/x/v2/reply/report"
API_RELATION_MODIFY = "https://api.bilibili.com/x/relation/modify"


class BiliAPI:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.http = HttpClient(cfg)
        self.me: Dict[str, Any] = {}
        self._wbi_main_broken = False

    # -- 账号 --------------------------------------------------------------
    def whoami(self, allow_anonymous: bool = False) -> Dict[str, Any]:
        try:
            payload = self.http.get(API_NAV)
        except BiliError as e:
            if allow_anonymous:
                return {}
            if e.code == -101:
                raise SystemExit(
                    "Cookie 无效或已过期（-101）。重新登录 B 站 → F12 复制完整 Cookie（含 SESSDATA、bili_jct）。"
                )
            raise SystemExit("nav 接口失败：%s" % e)
        data = payload.get("data") or {}
        if not data.get("isLogin"):
            if allow_anonymous:
                return {}
            raise SystemExit(
                "未登录。请确认 config.json 的 cookie 里有 SESSDATA、bili_jct、DedeUserID，"
                "并且没有过期（浏览器刷新一下 B 站，F12 重新复制）。"
            )
        self.me = {
            "mid": int(data.get("mid") or 0),
            "uname": data.get("uname") or "",
            "level": (data.get("level_info") or {}).get("current_level"),
            "vip": (data.get("vipStatus") or 0),
            "wbi_img": (data.get("wbi_img") or {}).get("img_url", ""),
        }
        if not self.cfg.get("mid"):
            self.cfg["mid"] = self.me["mid"]
        if int(self.cfg.get("mid") or 0) and int(self.cfg["mid"]) != self.me["mid"]:
            warn("config.mid=%s 与登录账号 mid=%s 不一致，按登录账号处理。"
                 % (self.cfg["mid"], self.me["mid"]))
            self.cfg["mid"] = self.me["mid"]
        return self.me

    # -- 投稿列表 ----------------------------------------------------------
    def list_own_videos(self, mid: int, pages: int = 2, page_size: int = 30) -> List[Dict[str, Any]]:
        found: Dict[str, Dict[str, Any]] = {}
        for pn in range(1, max(1, pages) + 1):
            params = self.http.wbi_params({
                "mid": mid, "pn": pn, "ps": page_size,
                "order": "pubdate", "platform": "web", "web_location": 1550101,
                # 下面几个是 B 站前端的指纹参数，随便填也比不带更容易过
                "dm_img_list": "[]", "dm_img_str": "",
                "dm_cover_img_str": "",
                "dm_img_inter": '{"ds":[],"wh":[0,0,0],"of":[0,0,0]}',
            })
            try:
                payload = self.http.get(API_SPACE_ARC, params,
                                        referer="https://space.bilibili.com/%s/video" % mid)
            except RiskControlError as e:
                warn("投稿列表被风控（%s），本轮跳过自动发现，用 config.videos 手动指定视频更稳。" % e.code)
                break
            except BiliError as e:
                warn("投稿列表接口失败：%s" % e)
                break
            vlist = (((payload.get("data") or {}).get("list") or {}).get("vlist")) or []
            if not vlist:
                break
            for v in vlist:
                bvid = v.get("bvid")
                if bvid and bvid not in found:
                    found[bvid] = {
                        "bvid": bvid,
                        "aid": int(v.get("aid") or 0),
                        "title": v.get("title") or "",
                        "created": int(v.get("created") or 0),
                        "comment": int(v.get("comment") or 0),
                    }
            page = (payload.get("data") or {}).get("page") or {}
            total = int(page.get("count") or 0)
            if total and pn * page_size >= total:
                break
            if len(vlist) < page_size:
                break
        return list(found.values())

    # -- 视频信息 ----------------------------------------------------------
    def video_info(self, bvid: str) -> Dict[str, Any]:
        payload = self.http.get(API_VIEW, {"bvid": bvid})
        data = payload.get("data") or {}
        return {
            "bvid": data.get("bvid") or bvid,
            "aid": int(data.get("aid") or 0),
            "title": data.get("title") or "",
            "owner_mid": int((data.get("owner") or {}).get("mid") or 0),
            "reply": int((data.get("stat") or {}).get("reply") or 0),
            "ctime": int(data.get("ctime") or 0),
        }

    # -- 主楼评论 ----------------------------------------------------------
    def fetch_main_page(self, oid: int, pn: int = 1, ps: int = 20,
                        sort: int = 0) -> Dict[str, Any]:
        """老分页接口，返回 {replies:[], top:{}, count:int, acount:int, is_end:bool}
        注意：这个接口的 ps 实际上限是 20，传更大也不会多给。"""
        ps = max(1, min(20, int(ps or 20)))
        payload = self.http.get(API_REPLY, {
            "type": 1, "oid": oid, "pn": pn, "ps": ps,
            "sort": sort, "nohot": 1, "plat": 1,
        })
        data = payload.get("data") or {}
        if data.get("replies") is None and data.get("page") is None:
            # 评论区关闭 / 视频不存在
            return {"replies": [], "top": {}, "count": 0, "acount": 0, "is_end": True}
        page = data.get("page") or {}
        replies = data.get("replies") or []
        return {
            "replies": replies,
            "top": data.get("top") or {},
            "count": int(page.get("count") or 0),
            "acount": int(page.get("acount") or 0),
            "is_end": len(replies) < ps,
        }

    def fetch_main_page_wbi(self, oid: int, offset: str = "", ps: int = 20,
                            mode: int = 3) -> Dict[str, Any]:
        """新版 /x/v2/reply/wbi/main，老接口被风控时的备用通道。mode=3 按时间倒序。"""
        pagination = {"offset": offset} if offset else {"offset": ""}
        params = self.http.wbi_params({
            "oid": oid, "type": 1, "mode": mode, "ps": ps,
            "pagination_str": json.dumps(pagination, separators=(",", ":"), ensure_ascii=False),
            "plat": 1, "seek_rpid": "", "web_location": 1315875,
        })
        payload = self.http.get(API_REPLY_WBI_MAIN, params,
                                referer="https://www.bilibili.com/video/av%d" % oid)
        data = payload.get("data") or {}
        cursor = data.get("cursor") or {}
        pr = cursor.get("pagination_reply") or {}
        replies = data.get("replies") or []
        return {
            "replies": replies,
            "top": data.get("top_replies") or {},
            "count": int((data.get("cursor") or {}).get("all_count") or 0),
            "acount": 0,
            "is_end": bool(cursor.get("is_end")) or not replies,
            "next_offset": pr.get("next_offset") or "",
        }

    # -- 楼中楼 ------------------------------------------------------------
    def fetch_sub_page(self, oid: int, root: int, pn: int = 1, ps: int = 20) -> Dict[str, Any]:
        payload = self.http.get(API_REPLY_SUB, {
            "type": 1, "oid": oid, "root": root, "pn": pn, "ps": ps, "plat": 1,
        })
        data = payload.get("data") or {}
        replies = data.get("replies") or []
        page = data.get("page") or {}
        return {
            "replies": replies,
            "count": int(page.get("count") or 0),
            "is_end": len(replies) < ps,
        }

    # -- 处置 --------------------------------------------------------------
    def delete_reply(self, oid: int, rpid: int) -> None:
        self.http.post(API_REPLY_DEL, {
            "oid": oid, "type": 1, "rpid": rpid, "csrf": self.http.csrf(),
        }, referer="https://www.bilibili.com/video/av%d" % oid)

    def report_reply(self, oid: int, rpid: int, reason: int) -> None:
        self.http.post(API_REPLY_REPORT, {
            "oid": oid, "type": 1, "rpid": rpid,
            "reason": int(reason), "csrf": self.http.csrf(),
        }, referer="https://www.bilibili.com/video/av%d" % oid)

    def block_user(self, mid: int) -> None:
        self.http.post(API_RELATION_MODIFY, {
            "fid": mid, "act": 5, "re_src": 11, "csrf": self.http.csrf(),
        }, referer="https://space.bilibili.com/%d" % mid)


# ---------------------------------------------------------------------------
# 规则引擎
# ---------------------------------------------------------------------------

TIER_FLAGS: Dict[str, Dict[str, bool]] = {
    # 小杯：只认强特征（昵称模板、品牌词、AI 残留、数值矩阵、重复洗稿），几乎没有误杀
    "cup": {"soft": False, "at": False, "punct": False, "char": False, "summary": False,
            "long": False, "ai_suffix": True, "level": True, "name_soft": True},
    # 标准：加上软词、@、总结模板、超长文 —— 适合直接删除
    "standard": {"soft": True, "at": True, "punct": False, "char": False, "summary": True,
                 "long": True, "ai_suffix": True, "level": True, "name_soft": True},
    # 杀戮：再把全角标点、破折号、直角引号、单字鱼鲸算上，误杀率明显上升
    "kill": {"soft": True, "at": True, "punct": True, "char": True, "summary": True,
             "long": True, "ai_suffix": True, "level": True, "name_soft": True},
}

RE_QUOTE_PREFIX = re.compile(r"^回复\s*@[\s\S]{0,40}?\s*[:：]\s*")
RE_PERCENT = re.compile(r"\d+\.\d+\s*(?:%|％|倍|万|亿|w)")
RE_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")
RE_AT = re.compile(r"@[\w\u4e00-\u9fff\-]{1,30}")


@dataclass
class Verdict:
    hit: bool = False
    score: int = 0
    rules: List[str] = field(default_factory=list)
    hard: bool = False

    def text(self) -> str:
        return ",".join(self.rules) if self.rules else "-"


@dataclass
class ReplyView:
    rpid: int
    mid: int
    uname: str
    message: str
    ctime: int
    like: int
    rcount: int
    is_top: bool
    level: int
    is_sub: bool = False
    root: int = 0

    def brief(self, n: int = 42) -> str:
        return "[%s] %s: %s" % (self.rpid, self.uname or "-", truncate(self.message, n))


def reply_view(raw: Dict[str, Any], is_sub: bool = False, root: int = 0) -> ReplyView:
    member = raw.get("member") or {}
    content = raw.get("content") or {}
    control = raw.get("reply_control") or {}
    level = int(((member.get("level_info") or {}).get("current_level")) or 0)
    return ReplyView(
        rpid=int(raw.get("rpid") or 0),
        mid=int(raw.get("mid") or member.get("mid") or 0),
        uname=str(member.get("uname") or ""),
        message=str(content.get("message") or ""),
        ctime=int(raw.get("ctime") or 0),
        like=int(raw.get("like") or 0),
        rcount=int(raw.get("rcount") or 0),
        is_top=bool(control.get("is_up_top")),
        level=level,
        is_sub=is_sub,
        root=int(raw.get("root") or root or 0),
    )


def _body_without_quote_prefix(message: str) -> str:
    """楼中楼的「回复 @某某某 :」是 B 站自动加的，判 @ 之前先剥掉。"""
    if message.startswith("回复"):
        m = RE_QUOTE_PREFIX.match(message)
        if m:
            return message[m.end():]
    return message


def judge(v: ReplyView, cfg: Dict[str, Any], up_mid: int = 0,
          dup_counts: Optional[Dict[str, int]] = None) -> Verdict:
    vt = Verdict()
    msg = v.message or ""
    uname = v.uname or ""
    flags = TIER_FLAGS.get(str(cfg.get("tier") or "standard"), TIER_FLAGS["standard"])

    # ---- 0) 直接放行的对象 ----------------------------------------------
    if v.rpid == 0 or v.mid == 0:
        return vt
    white_mids = {int(x) for x in as_list(cfg.get("whitelist_mids")) if str(x).strip().isdigit()}
    white_rpids = {int(x) for x in as_list(cfg.get("whitelist_rpids")) if str(x).strip().isdigit()}
    if int(v.mid) in white_mids:
        return vt
    if v.rpid in white_rpids:
        return vt
    if v.is_top and cfg.get("skip_pinned", True):
        return vt
    if up_mid and v.mid == int(up_mid) and cfg.get("skip_self", True):
        return vt  # UP 主自己的评论 / 置顶说明

    # ---- 1) 自定义词：命中即删，不参与打分 ------------------------------
    for w in as_list(cfg.get("custom_words")):
        w = str(w).strip()
        if w and (w.lower() in msg.lower() or w.lower() in uname.lower()):
            vt.hit, vt.hard, vt.score = True, True, 100
            vt.rules.append("custom:%s" % w)
            return vt

    # ---- 2) 硬命中 ------------------------------------------------------
    for pat in as_list(cfg.get("name_patterns")):
        try:
            if pat and re.search(str(pat), uname):
                vt.hit, vt.hard, vt.score = True, True, 100
                vt.rules.append("name:/%s/" % pat)
                return vt
        except re.error:
            warn("name_patterns 里的正则写错了，已跳过：%r" % pat)

    for w in as_list(cfg.get("brand_words")):
        if w and str(w) in msg:
            vt.hit, vt.hard, vt.score = True, True, 100
            vt.rules.append("brand:%s" % w)
            return vt

    for w in as_list(cfg.get("prompt_words")):
        if w and str(w).lower() in msg.lower():
            vt.hit, vt.hard, vt.score = True, True, 100
            vt.rules.append("prompt:%s" % w)
            return vt

    hits = RE_PERCENT.findall(msg)
    if len(hits) >= int(cfg.get("numeric_matrix_min") or 3):
        vt.hit, vt.hard, vt.score = True, True, 100
        vt.rules.append("numeric:%d项(%s)" % (len(hits), "/".join(hits[:4])))
        return vt

    norm = normalize_text(msg)
    dup_n = int((dup_counts or {}).get(norm, 0))
    if norm and len(norm) >= int(cfg.get("duplicate_min_len") or 12) and dup_n >= 3:
        vt.hit, vt.hard, vt.score = True, True, 100
        vt.rules.append("dup_matrix:%d个不同账号同文" % dup_n)
        return vt

    # ---- 3) 软信号累加 --------------------------------------------------
    # 昵称里带单个「鱼/鲸」只加分：真人叫「章鱼幻想」「鲨鱼风暴辣椒」的太多了，
    # 光凭这个字判死会误删。要配合正文特征（@、总结模板、口癖…）才过线。
    if flags.get("name_soft", True):
        for pat in as_list(cfg.get("name_soft_patterns")):
            try:
                if pat and re.search(str(pat), uname):
                    vt.score += 40
                    vt.rules.append("name_soft+40(/%s/)" % pat)
                    break
            except re.error:
                continue

    soft = cfg.get("soft_words") or {}
    if isinstance(soft, str):
        soft = {soft: 20}
    elif isinstance(soft, (list, tuple)):
        soft = {str(w): 20 for w in soft}
    elif not isinstance(soft, dict):
        warn("soft_words 类型不对（应为对象），本轮忽略。")
        soft = {}
    if flags.get("soft", True):
        # 按词长降序、已匹配区间不重复计分（否则「本喵」会连「喵」一起算）
        covered = [False] * len(msg)
        for w, weight in sorted(soft.items(), key=lambda kv: -len(str(kv[0]))):
            w = str(w)
            start = 0
            while w:
                idx = msg.find(w, start)
                if idx < 0:
                    break
                if any(covered[idx:idx + len(w)]):
                    start = idx + 1
                    continue
                for j in range(idx, idx + len(w)):
                    covered[j] = True
                vt.score += int(weight)
                vt.rules.append("soft:%s+%d" % (w, int(weight)))
                break

    body = _body_without_quote_prefix(msg)
    if flags.get("at") and RE_AT.search(body):
        vt.score += 20
        vt.rules.append("at+20")

    if flags.get("punct"):
        punct_hits = len(re.findall(r"[：；]", msg))
        if punct_hits >= 3:
            vt.score += 15
            vt.rules.append("fw_punct+15")
        if re.search(r"[\u2014\u2015\u2500]|--", msg):
            vt.score += 15
            vt.rules.append("dash+15")
        if re.search(r"[「」]", msg):
            vt.score += 15
            vt.rules.append("corner+15")

    if flags.get("char") and re.search(r"[鱼鲸]", msg):
        vt.score += 30
        vt.rules.append("char+30")

    if flags.get("summary"):
        summary_hits = [w for w in as_list(cfg.get("summary_words")) if w and str(w) in msg]
        need = int(cfg.get("summary_min_hits") or 2)
        if len(summary_hits) >= need:
            # 封顶 40：单条「总结模板」永远不足以自己过 60 分阈值，避免误删真人长评
            gain = 40
            vt.score += gain
            vt.rules.append("summary+%d(%s)" % (gain, ",".join(str(x) for x in summary_hits[:4])))

    if flags.get("long") and len(msg) >= int(cfg.get("long_text_len") or 300):
        vt.score += 15
        vt.rules.append("long+15(%d字)" % len(msg))

    if flags.get("ai_suffix"):
        for pat in as_list(cfg.get("ai_name_suffix")):
            try:
                if pat and re.search(str(pat), uname):
                    vt.score += 25
                    vt.rules.append("ai_name+25(/%s/)" % pat)
                    break
            except re.error:
                continue

    if flags.get("level") and v.level <= 1 and len(msg) >= 30:
        vt.score += 10
        vt.rules.append("low_level+10")

    if len(RE_EMOJI.findall(msg)) >= 5:
        vt.score += 5
        vt.rules.append("emoji+5")

    if vt.score >= int(cfg.get("threshold") or 60):
        vt.hit = True
    return vt


def duplicate_index(views: Iterable[ReplyView], min_len: int = 12) -> Dict[str, int]:
    """同屏文本聚类：归一化文本 -> 不同 mid 数量（洗稿矩阵特征）。"""
    buckets: Dict[str, set] = {}
    for v in views:
        norm = normalize_text(v.message)
        if not norm or len(norm) < min_len:
            continue
        buckets.setdefault(norm, set()).add(v.mid)
    return {k: len(s) for k, s in buckets.items()}


# ---------------------------------------------------------------------------
# 状态与日志
# ---------------------------------------------------------------------------

class State:
    """记录每个视频处理到哪儿，避免重启后重复删/重复举报。"""

    HANDLED_CAP = 20000

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        raw: Any = {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            raw = {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            bad = "%s.corrupt-%d" % (self.path, now_ts())
            try:
                os.replace(self.path, bad)
            except OSError:
                pass
            warn("state 文件损坏，已备份到 %s 并重建。" % os.path.basename(bad))
            raw = {}
        except OSError as e:
            warn("读取 state 失败（%s），本轮按空状态处理。" % e)
            raw = {}
        if not isinstance(raw, dict):
            warn("state 文件结构不对（顶层不是对象），已重建。")
            raw = {}
        for key in ("videos", "blocked_mids", "stats"):
            if not isinstance(raw.get(key), dict):
                raw[key] = {}
        for key in ("deleted", "reported", "blocked", "failed", "would", "gone"):
            raw["stats"].setdefault(key, 0)
        return raw

    def video(self, key: str) -> Dict[str, Any]:
        videos = self.data["videos"]
        rec = videos.get(key)
        if not isinstance(rec, dict):
            rec = None
        if rec is None:
            rec = {"aid": 0, "title": "", "handled": {}, "last_seen_ctime": 0,
                   "hits": {}, "fails": {}}
            videos[key] = rec
        rec.setdefault("aid", 0)
        rec.setdefault("title", "")
        rec.setdefault("last_seen_ctime", 0)
        for field_name in ("handled", "hits", "fails"):
            if not isinstance(rec.get(field_name), dict):
                rec[field_name] = {}
        return rec

    def is_handled(self, key: str, rpid: int) -> bool:
        return str(rpid) in self.video(key)["handled"]

    def handled_action(self, key: str, rpid: int) -> str:
        rec = self.video(key)["handled"].get(str(rpid))
        return str(rec.get("act") or "") if isinstance(rec, dict) else ""

    def mark(self, key: str, rpid: int, ctime: int, action: str) -> None:
        """记录已处置的评论。注意：不碰 last_seen_ctime（游标由 scan_video 统一推进）。"""
        rec = self.video(key)
        rec["handled"][str(rpid)] = {"ctime": int(ctime), "act": action, "at": now_ts()}
        rec.get("fails", {}).pop(str(rpid), None)
        if len(rec["handled"]) > self.HANDLED_CAP:
            items = sorted(rec["handled"].items(), key=lambda kv: kv[1].get("ctime", 0))
            rec["handled"] = dict(items[-self.HANDLED_CAP:])

    def note_fail(self, key: str, rpid: int) -> int:
        rec = self.video(key)
        fails = rec.setdefault("fails", {})
        n = int(fails.get(str(rpid), 0)) + 1
        fails[str(rpid)] = n
        if len(fails) > 5000:
            rec["fails"] = dict(list(fails.items())[-5000:])
        return n

    def bump_hit(self, key: str, mid: int) -> int:
        rec = self.video(key)
        hits = rec.setdefault("hits", {})
        hits[str(mid)] = int(hits.get(str(mid), 0)) + 1
        return hits[str(mid)]

    def hit_count(self, key: str, mid: int) -> int:
        return int(self.video(key).get("hits", {}).get(str(mid), 0))

    def incr(self, field: str, n: int = 1) -> None:
        self.data["stats"][field] = int(self.data["stats"].get(field, 0)) + n

    def save(self) -> None:
        save_json(self.path, self.data)


class EventLog:
    """events.jsonl：一行一条，方便事后审计「为什么删了这条」。"""

    def __init__(self, path: str, enabled: bool = True):
        self.path = os.path.abspath(path)
        self.enabled = enabled
        self.rows: List[Dict[str, Any]] = []

    def add(self, **kw: Any) -> None:
        rec = {"ts": now_ts(), "time": fmt_time(now_ts())}
        rec.update(kw)
        self.rows.append(rec)
        if not self.enabled:
            return
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def tail(self, n: int = 20) -> List[Dict[str, Any]]:
        return self.rows[-n:]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

class Guard:
    def __init__(self, cfg: Dict[str, Any], api: Optional[BiliAPI] = None,
                 dry_run: Optional[bool] = None, stop_event: Optional[Any] = None,
                 on_round: Optional[Any] = None):
        self.cfg = cfg
        self.api = api or BiliAPI(cfg)
        self.state = State(cfg.get("state_file") or DEFAULT_STATE)
        self.events = EventLog(cfg.get("log_file") or DEFAULT_LOG)
        self.stop_event = stop_event      # GUI / 外部控制停止
        self.on_round = on_round          # 每轮结束回调(totals)
        if dry_run is None:
            raw_flag = cfg.get("dry_run", True)
            if not isinstance(raw_flag, (bool, str)):
                warn("dry_run 的取值是 %r，既不是 true/false 也不是字符串；按 dry-run 处理，不会删任何东西。"
                     % (raw_flag,))
            self.dry_run = not is_live_flag(raw_flag)
        else:
            self.dry_run = bool(dry_run)
        self.up_mid = int(cfg.get("mid") or 0)
        self.cutoff = 0
        self.full_history = False
        self.totals = {"scanned": 0, "hit": 0, "deleted": 0, "would": 0,
                       "failed": 0, "reported": 0, "blocked": 0, "gone": 0}

    # -- 目标视频 ----------------------------------------------------------
    def resolve_videos(self) -> List[Dict[str, Any]]:
        videos: List[Dict[str, Any]] = []
        seen = set()
        for raw in as_list(self.cfg.get("videos")):
            s = str(raw).strip()
            if not s:
                continue
            bvid, aid = "", 0
            if re.fullmatch(r"BV[0-9A-Za-z]{10}", s):
                bvid = s
            elif re.fullmatch(r"\d+", s):
                aid = int(s)
            elif re.fullmatch(r"av\d+", s, re.I):
                aid = int(s[2:])
            else:
                m = re.search(r"(BV[0-9A-Za-z]{10})", s) or re.search(r"av(\d+)", s, re.I)
                if m:
                    grp = m.group(0)
                    if grp.lower().startswith("av"):
                        aid = int(grp[2:])
                    else:
                        bvid = grp
            key = bvid or ("av%d" % aid)
            if not (bvid or aid) or key in seen:
                if not (bvid or aid):
                    warn("config.videos 里的 %r 既不是 bvid 也不是 aid，已跳过。" % s)
                continue
            seen.add(key)
            videos.append({"bvid": bvid, "aid": aid, "title": ""})
        if self.cfg.get("auto_discover", True):
            if not self.up_mid:
                warn("config.mid 为空且未登录，跳过自动发现投稿。")
            else:
                found = self.api.list_own_videos(
                    self.up_mid,
                    pages=int(self.cfg.get("discover_pages") or 2),
                )
                for v in found:
                    if v["bvid"] in seen:
                        continue
                    seen.add(v["bvid"])
                    videos.append(v)
                log("自动发现投稿 %d 个，加上手动指定共 %d 个视频。" % (len(found), len(videos)))
        return videos

    # -- 单视频扫描 --------------------------------------------------------
    def scan_video(self, item: Dict[str, Any]) -> Dict[str, int]:
        bvid = item.get("bvid") or ""
        aid = int(item.get("aid") or 0)
        if not aid and bvid:
            try:
                info = self.api.video_info(bvid)
                aid = info["aid"]
                item.update({"aid": aid, "title": info["title"] or item.get("title", "")})
                if not self.up_mid:
                    self.up_mid = int(info.get("owner_mid") or 0)
            except BiliError as e:
                warn("取视频信息失败 %s：%s" % (bvid, e))
                return {}
        if not aid:
            warn("跳过无效视频项：%r" % item)
            return {}

        rec = self.state.video(bvid or ("av%d" % aid))
        rec["aid"] = aid
        if item.get("title"):
            rec["title"] = item["title"]
        title = rec.get("title") or bvid

        ps = int(self.cfg.get("page_size") or 20)
        max_pages = int(self.cfg.get("max_pages") or 8)
        lookback = int(self.cfg.get("lookback_hours") or 72) * 3600
        cutoff = now_ts() - lookback
        # full_history = 忽略时间窗口，把所有可见主楼都扫一遍（首次清历史用）
        self.full_history = bool(self.cfg.get("full_history"))
        self.cutoff = 0 if self.full_history else cutoff
        # 游标 = 上一轮扫描到的最老评论时间；下一轮只看比它新的评论
        cursor = int(rec.get("last_seen_ctime") or 0)
        first_run = cursor <= 0
        stop_at = 0 if self.full_history else (cutoff if first_run else cursor)

        log("扫描 %s  %s%s" % (bvid or ("av%d" % aid), truncate(title, 40),
                              "（全量历史）" if self.full_history else ""))
        stats = {"scanned": 0, "hit": 0, "deleted": 0, "would": 0, "failed": 0,
                 "reported": 0, "blocked": 0, "gone": 0, "pages": 0, "total": 0}

        main_views: List[ReplyView] = []
        scanned_ctimes: List[int] = []
        truncated = False
        use_wbi_main = False
        wbi_mode = int(self.cfg.get("wbi_mode") or 3)
        offset = ""

        for pn in range(1, max_pages + 1):
            try:
                if use_wbi_main:
                    page = self.api.fetch_main_page_wbi(aid, offset=offset, ps=ps, mode=wbi_mode)
                    offset = page.get("next_offset") or ""
                else:
                    page = self.api.fetch_main_page(aid, pn=pn, ps=ps, sort=0)
            except RiskControlError as e:
                warn("主楼接口风控（%s），切换到 wbi/main 备用通道。" % e.code)
                use_wbi_main, offset = True, ""
                if pn == 1:
                    try:
                        page = self.api.fetch_main_page_wbi(aid, offset="", ps=ps, mode=wbi_mode)
                        offset = page.get("next_offset") or ""
                    except BiliError as e2:
                        warn("备用通道也失败：%s，本视频本轮放弃。" % e2)
                        break
                else:
                    break
            except BiliError as e:
                warn("拉评论失败 %s 第 %d 页：%s" % (bvid, pn, e))
                break

            stats["pages"] += 1
            replies = page.get("replies") or []
            stats["total"] = max(stats["total"], int(page.get("count") or 0))

            # 老接口对未登录/被风控的请求会给 code=0 + 空列表，这里自适应切到
            # 前端在用的 wbi/main，避免「视频明明有几十万评论却扫到 0 条」
            if pn == 1 and not replies and not use_wbi_main:
                try:
                    alt = self.api.fetch_main_page_wbi(aid, offset="", ps=ps, mode=wbi_mode)
                except BiliError as e:
                    warn("wbi/main 通道也不可用：%s" % e)
                    alt = None
                if alt and alt.get("replies"):
                    log("  老接口返回空评论，已切换 wbi/main 通道。")
                    use_wbi_main, offset = True, alt.get("next_offset") or ""
                    page, replies = alt, alt["replies"]

            if not replies:
                break

            page_ctimes = [int(r.get("ctime") or 0) for r in replies if r.get("ctime")]
            if page_ctimes:
                scanned_ctimes.extend(page_ctimes)

            for raw in replies:
                rv = reply_view(raw)
                if not rv.rpid:
                    continue
                main_views.append(rv)

            if page.get("is_end"):
                break
            # 已经翻到上一轮处理位置（或首次运行的回溯窗口边界）就收手
            if page_ctimes and min(page_ctimes) <= stop_at:
                break
        else:
            # for 循环正常跑完 = 翻满了 max_pages 还没追平
            truncated = True

        # 同屏重复文本聚类（洗稿矩阵）
        dup = duplicate_index(main_views, int(self.cfg.get("duplicate_min_len") or 12))

        for rv in main_views:
            key = bvid or ("av%d" % aid)
            # 先扫楼中楼（主楼处理完会被标记，之后就会被跳过）；
            # 主楼删除失败时仍然扫，否则这棵子树会被永久跳过
            if self.cfg.get("include_sub_replies", True) and rv.rcount > 0:
                act = self.state.handled_action(key, rv.rpid)
                if act not in ("deleted", "gone", "gave_up"):
                    self._scan_sub(rv, aid, bvid, stats)
            self._process(rv, aid, bvid, stats, dup)

        if truncated:
            warn("  评论量大，翻满 %d 页仍未追平；建议调大 max_pages 或缩短轮询间隔。" % max_pages)
        elif self.full_history:
            # 全量模式下不推进游标：下次仍然全量扫（想回到增量就把 full_history 关掉）
            rec["last_seen_ctime"] = 0
        elif scanned_ctimes and (not use_wbi_main or wbi_mode == 2):
            # 只有完整追平、且排序确定是按时间时才推进游标；
            # 截断或排序不确定（mode!=2 可能是热度序）时保持旧游标，下轮重来，避免漏扫
            rec["last_seen_ctime"] = min(scanned_ctimes)

        log("  评论总数 %(total)d（含楼中楼） / 页数 %(pages)d / 扫描 %(scanned)d / 命中 %(hit)d / "
            "删除 %(deleted)d / 待删 %(would)d / 失败 %(failed)d / 举报 %(reported)d / 拉黑 %(blocked)d" % stats)
        for k in ("scanned", "hit", "deleted", "would", "failed", "reported", "blocked", "gone"):
            self.totals[k] += stats.get(k, 0)
        return stats

    def _scan_sub(self, parent: ReplyView, aid: int, bvid: str, stats: Dict[str, int]) -> None:
        """楼中楼默认按时间正序返回（旧→新），所以只翻最后几页才是最新回复。"""
        ps = 20
        keep = max(1, int(self.cfg.get("sub_reply_pages") or 2))
        try:
            first = self.api.fetch_sub_page(aid, parent.rpid, pn=1, ps=ps)
        except BiliError as e:
            warn("拉楼中楼失败 rpid=%s：%s" % (parent.rpid, e))
            return
        count = int(first.get("count") or parent.rcount or 0)
        total_pages = max(1, (count + ps - 1) // ps)
        start_page = max(1, total_pages - keep + 1)

        views: List[ReplyView] = []
        for pn in range(start_page, total_pages + 1):
            if pn == 1:
                page = first
            else:
                try:
                    page = self.api.fetch_sub_page(aid, parent.rpid, pn=pn, ps=ps)
                except BiliError as e:
                    warn("拉楼中楼失败 rpid=%s 第 %d 页：%s" % (parent.rpid, pn, e))
                    break
            for raw in (page.get("replies") or []):
                views.append(reply_view(raw, is_sub=True, root=parent.rpid))
            if page.get("is_end") and pn >= total_pages - 1:
                break
        if not views:
            return
        dup = duplicate_index(views, int(self.cfg.get("duplicate_min_len") or 12))
        for v in views:
            self._process(v, aid, bvid, stats, dup)

    # -- 单条裁定 + 处置 ---------------------------------------------------
    def _process(self, v: ReplyView, aid: int, bvid: str,
                 stats: Dict[str, int], dup: Dict[str, int]) -> None:
        key = bvid or ("av%d" % aid)
        if self.state.is_handled(key, v.rpid):
            return
        # 超出回溯窗口的历史评论不处理（否则 lookback_hours 形同虚设）
        if self.cutoff and v.ctime and v.ctime < self.cutoff:
            return
        stats["scanned"] += 1
        vt = judge(v, self.cfg, up_mid=self.up_mid, dup_counts=dup)
        if not vt.hit:
            return
        stats["hit"] += 1
        action, extra = self._dispose(v, vt, aid, key)
        if action in ("deleted", "would", "failed", "gone"):
            stats[action] = stats.get(action, 0) + 1
        if extra.get("reported"):
            stats["reported"] = stats.get("reported", 0) + 1
        if extra.get("blocked"):
            stats["blocked"] = stats.get("blocked", 0) + 1

        if action in ("deleted", "gone"):
            self.state.mark(key, v.rpid, v.ctime, action)
        elif action == "failed":
            # 失败了不记 handled，下一轮重试；连续 3 次不行就放弃，免得死循环打接口
            fails = self.state.note_fail(key, v.rpid)
            if fails >= 3:
                self.state.mark(key, v.rpid, v.ctime, "gave_up")
                warn("  放弃重试 rpid=%s（连续失败 %d 次）" % (v.rpid, fails))
        # dry-run 的 would / 失败都不写 handled：
        # 否则「先体检一天再开删」会一条都删不掉

        if action == "deleted":
            self.state.bump_hit(key, v.mid)

    def _sleep_gap(self, scale: float = 1.0) -> None:
        lo = float(self.cfg.get("request_min_delay") or 0.8) * scale
        hi = float(self.cfg.get("request_max_delay") or 1.6) * scale
        jitter_sleep(lo, hi)

    def _dispose(self, v: ReplyView, vt: Verdict, aid: int, key: str) -> Tuple[str, Dict[str, Any]]:
        extra: Dict[str, Any] = {"reported": False, "blocked": False}
        reason_label = (vt.rules[0] if (vt.hard and vt.rules) else ",".join(vt.rules[:2])) or "-"

        if self.dry_run:
            log("  [DRY] 命中 %s | score=%d | %s" % (reason_label, vt.score, v.brief(60)))
            self.events.add(action="would_delete", aid=aid, rpid=v.rpid, mid=v.mid,
                            uname=v.uname, message=v.message, rules=vt.rules,
                            score=vt.score, dry_run=True)
            return "would", extra

        # 1) 先举报（举报要求评论还在），失败不影响删除，但要落盘
        if self.cfg.get("report", False):
            try:
                self._sleep_gap(1.5)
                self.api.report_reply(aid, v.rpid,
                                      int(self.cfg.get("report_reason") or DEFAULT_REPORT_REASON))
                extra["reported"] = True
                self.state.incr("reported", 1)
            except BiliError as e:
                warn("举报失败 rpid=%s：%s" % (v.rpid, e))
                extra["report_error"] = str(e)
                self.state.incr("report_failed", 1)
                self.events.add(action="report_failed", aid=aid, rpid=v.rpid, mid=v.mid,
                                uname=v.uname, rules=vt.rules, score=vt.score,
                                code=e.code, error=e.message)

        # 2) 删除
        try:
            self.api.delete_reply(aid, v.rpid)
        except BiliError as e:
            if e.code == -404:
                self.events.add(action="already_gone", aid=aid, rpid=v.rpid, mid=v.mid,
                                uname=v.uname, message=v.message, rules=vt.rules, score=vt.score)
                return "gone", extra
            warn("删除失败 rpid=%s：%s" % (v.rpid, e))
            self.events.add(action="delete_failed", aid=aid, rpid=v.rpid, mid=v.mid,
                            uname=v.uname, message=v.message, rules=vt.rules,
                            score=vt.score, code=e.code, error=e.message)
            return "failed", extra

        self.state.incr("deleted", 1)
        log("  已删 %s | score=%d | %s" % (reason_label, vt.score, v.brief(60)))
        self.events.add(action="deleted", aid=aid, rpid=v.rpid, mid=v.mid, uname=v.uname,
                        message=v.message, rules=vt.rules, score=vt.score,
                        reported=extra["reported"])

        # 3) 同一账号反复命中 -> 拉黑（第二层：让他以后根本发不出来）
        if self.cfg.get("auto_block", False) and v.mid:
            need = max(1, int(self.cfg.get("block_after_hits") or 3))
            # 当前这条还没计入 hit_count（bump 在 _process 里），所以要 +1
            hits = self.state.hit_count(key, v.mid) + 1
            if hits >= need and str(v.mid) not in self.state.data.get("blocked_mids", {}):
                try:
                    self._sleep_gap(1.2)
                    self.api.block_user(v.mid)
                    self.state.data.setdefault("blocked_mids", {})[str(v.mid)] = {
                        "at": now_ts(), "uname": v.uname, "hits": hits,
                    }
                    self.state.incr("blocked", 1)
                    extra["blocked"] = True
                    log("  已拉黑 %s(mid=%s)，累计命中 %d 次" % ((v.uname or "-"), v.mid, hits))
                    self.events.add(action="blocked", mid=v.mid, uname=v.uname, hits=hits)
                except BiliError as e:
                    warn("拉黑失败 mid=%s：%s" % (v.mid, e))
        return "deleted", extra

    # -- 整轮 --------------------------------------------------------------
    def _stopped(self) -> bool:
        return bool(self.stop_event is not None and self.stop_event.is_set())

    def run_once(self) -> Dict[str, int]:
        videos = self.resolve_videos()
        if not videos:
            warn("没有可监控的视频：请在 config.videos 里列 bvid，或确认自动发现可用。")
            return self.totals
        self.totals = {k: 0 for k in self.totals}
        for item in videos:
            if self._stopped():
                log("收到停止信号，本轮提前结束。")
                break
            try:
                self.scan_video(item)
            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except Exception as e:  # 单视频出错不影响其它视频
                warn("扫描 %s 出错：%r" % (item.get("bvid") or item.get("aid"), e))
            finally:
                self.state.save()
        self.state.save()
        if getattr(self.api, "auth_failed", False) or getattr(getattr(self.api, "http", None), "auth_failed", False):
            raise SystemExit("Cookie 已失效（-101），请重新登录 B 站并更新 config.json 的 cookie。")
        log("本轮汇总：%s" % json.dumps(self.totals, ensure_ascii=False))
        return self.totals

    def run_loop(self, once: bool = False) -> None:
        interval = max(20, int(self.cfg.get("interval_seconds") or 120))
        while not self._stopped():
            try:
                self.run_once()
            except KeyboardInterrupt:
                log("收到中断，退出。")
                return
            except SystemExit as e:
                warn(str(e))
                if self.on_round:
                    try:
                        self.on_round(self.totals, str(e))
                    except Exception:
                        pass
                return
            if self.on_round:
                try:
                    self.on_round(self.totals, None)
                except Exception:
                    pass
            if once:
                return
            sleep_s = interval + random.randint(0, max(1, interval // 10))
            log("%d 秒后进入下一轮。" % sleep_s)
            deadline = time.time() + sleep_s
            while time.time() < deadline:
                if self._stopped():
                    log("已停止。")
                    return
                time.sleep(min(0.5, max(0.0, deadline - time.time())))
        log("已停止。")


# ---------------------------------------------------------------------------
# 离线自测：不联网、不要 Cookie，验证规则引擎的判断是否符合预期
# ---------------------------------------------------------------------------

def _mk(rpid: int, mid: int, uname: str, message: str, **kw: Any) -> Dict[str, Any]:
    return {
        "rpid": rpid, "mid": mid, "ctime": kw.get("ctime", now_ts()),
        "like": 0, "rcount": kw.get("rcount", 0),
        "content": {"message": message},
        "member": {"uname": uname, "level_info": {"current_level": kw.get("level", 4)}},
        "reply_control": {"is_up_top": kw.get("top", False)},
    }


def selftest() -> int:
    base = merge_config({})
    up_mid = 1
    cases: List[Tuple[str, Dict[str, Any], Dict[str, Any], bool, Optional[Dict[str, int]]]] = [
        ("昵称大肥鱼 -> 硬命中", _mk(101, 5001, "旅行者的大肥鱼", "这视频真不错"), {}, True, None),
        ("昵称含单字鲸 -> 只加分", _mk(102, 5002, "深海的鲸", "来了"), {}, False, None),
        ("真人昵称章鱼幻想 -> 不命中", _mk(103, 5003, "章鱼幻想", "这猫粮看着不错"), {}, False, None),
        ("真人昵称鲨鱼辣椒 -> 不命中", _mk(104, 5004, "鲨鱼风暴辣椒", "上桌就用水壶喷水，喷他脸上"), {}, False, None),
        ("昵称含鱼 + @ + 总结 -> 命中", _mk(105, 5005, "鱼丙丙",
                                    "首先这个观点很有意思 @李大锤 其次数据也对得上"), {}, True, None),
        ("品牌词 本鲸", _mk(106, 5006, "路人", "本鲸今天也来打卡"), {}, True, None),
        ("Prompt 残留", _mk(107, 5007, "路人", "作为一个语言模型，我认为这期视频很有价值"),
         {}, True, None),
        ("数值矩阵", _mk(108, 5008, "路人", "效率提升 96.5%，成本降低 12.3倍，覆盖 2.2万 用户"),
         {}, True, None),
        ("真人聊鱼 -> 不命中", _mk(109, 5009, "老王", "这条鱼好大，钓上来得有多重啊"), {}, False, None),
        ("真人 @ -> 不命中", _mk(110, 5010, "老王", "@张三 你说得对"), {}, False, None),
        ("总结模板 + @ -> 命中", _mk(111, 5011, "路人", "首先这个观点很有意思 @李大锤 其次数据也对得上"),
         {}, True, None),
        ("置顶评论 -> 跳过", _mk(112, 5012, "xx的大肥鱼", "本鲸置顶", top=True), {}, False, None),
        ("白名单 mid -> 跳过", _mk(113, 777, "xx的大肥鱼", "本鲸来了"),
         {"whitelist_mids": [777]}, False, None),
        ("自定义词 -> 命中", _mk(114, 5014, "路人", "这段内容明显是洗稿"),
         {"custom_words": ["洗稿"]}, True, None),
        ("重复矩阵 -> 命中", _mk(115, 5015, "路人", "这期视频的总结写得太到位了我很喜欢"),
         {}, True, {"这期视频的总结写得太到位了我很喜欢": 3}),
        ("小杯档 @ 不命中", _mk(116, 5016, "老王", "@张三 你说得对"),
         {"tier": "cup"}, False, None),
        ("杀戮档标点 -> 命中", _mk(117, 5017, "老王", "「这鱼：好看；真的；不错——」"),
         {"tier": "kill"}, True, None),
        ("长文总结模板 -> 命中", _mk(118, 5018, "路人",
                              "首先" + "这期视频讲了很多有意思的东西，" * 24 + "其次" + "最后" + "总结一下",
                              level=0), {}, True, None),
        ("普通真人评论 -> 不命中", _mk(119, 5019, "老王", "三连了，下期想看这个系列的续集"), {}, False, None),
    ]

    fails = 0
    for name, raw, override, expect, dup in cases:
        cfg = merge_config(override)
        v = reply_view(raw)
        vt = judge(v, cfg, up_mid=up_mid, dup_counts=dup)
        ok = (vt.hit == expect)
        if not ok:
            fails += 1
        print("%s  %-22s expect=%-5s got=%-5s score=%-3d %s" % (
            "PASS" if ok else "FAIL", name, expect, vt.hit, vt.score, vt.text()))

    # wbi 签名：确定性 + 结构
    p1 = wbi_sign({"mid": 2, "pn": 1, "ps": 30, "a": "x!y"}, "7cd084941338484aae1ad9425b84077c",
                  "4932caff0ff746eab6f01bf08b70ac45", wts=1700000000)
    p2 = wbi_sign({"a": "x!y", "ps": 30, "pn": 1, "mid": 2}, "7cd084941338484aae1ad9425b84077c",
                  "4932caff0ff746eab6f01bf08b70ac45", wts=1700000000)
    sig_ok = (p1 == p2 and len(p1.get("w_rid", "")) == 32 and p1["a"] == "xy")
    print("%s  %-22s %s" % ("PASS" if sig_ok else "FAIL", "wbi 签名", p1.get("w_rid")))
    if not sig_ok:
        fails += 1

    print("\n%d 个用例，失败 %d 个。" % (len(cases) + 1, fails))
    return 0 if fails == 0 else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def ensure_utf8_stdio() -> None:
    """控制台编码兜底：保留原编码（中文 Windows 是 GBK，能正常输出中文），
    只把错误处理换成 replace，避免 emoji / 生僻字把整个程序打崩。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def load_config(path: str) -> Dict[str, Any]:
    raw = load_json(path, None)
    if raw is None:
        raise SystemExit(
            "找不到配置文件 %s。先运行：python fxxk_off_dfy.py init" % path
        )
    cfg = merge_config(raw)
    # 相对路径统一按配置文件所在目录解析，避免换个目录启动就把 state/日志写到别处
    base = os.path.dirname(os.path.abspath(path))
    for key in ("state_file", "log_file"):
        val = str(cfg.get(key) or "")
        if val and not os.path.isabs(val):
            cfg[key] = os.path.normpath(os.path.join(base, val))
    return cfg


def cmd_init(args: argparse.Namespace) -> int:
    path = args.config
    cookie = (args.cookie or "").strip()
    if os.path.exists(path) and not args.force:
        print("已存在 %s，未覆盖（要重建请加 --force）。" % path)
        return 1
    data = json.loads(json.dumps(DEFAULT_CONFIG_DATA))
    if cookie:
        data["cookie"] = cookie
    save_json(path, data)
    print("已生成 %s" % path)
    print("下一步：")
    print("  1) 打开 https://www.bilibili.com 并登录，F12 → 网络 → 任意 api 请求 → 复制整条 Cookie")
    print("  2) 填进 %s 的 cookie 字段（必须有 SESSDATA 和 bili_jct）" % os.path.basename(path))
    print("  3) 跑一次体检：python fxxk_off_dfy.py check     （只看不删）")
    print("  4) 确认无误后把 dry_run 改成 false，再：python fxxk_off_dfy.py run")
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    api = BiliAPI(cfg)
    me = api.whoami()
    print("登录账号：%s (mid=%s) 等级 Lv%s" % (me["uname"], me["mid"], me.get("level")))
    print("Cookie 长度：%d；bili_jct：%s" % (len(cfg.get("cookie") or ""),
                                       "有" if re.search(r"bili_jct=", cfg.get("cookie") or "") else "缺失"))
    vids = api.list_own_videos(int(me["mid"]), pages=1)
    print("最近投稿 %d 个：" % len(vids))
    for v in vids[:10]:
        print("  %-14s %s" % (v["bvid"], truncate(v["title"], 40)))
    return 0


def _apply_overrides(cfg: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    if getattr(args, "video", None):
        cfg["videos"] = list(args.video)
    if getattr(args, "tier", None):
        cfg["tier"] = args.tier
    if getattr(args, "threshold", None) is not None:
        cfg["threshold"] = int(args.threshold)
    if getattr(args, "interval", None) is not None:
        cfg["interval_seconds"] = int(args.interval)
    if getattr(args, "report", False):
        cfg["report"] = True
    if getattr(args, "block", False):
        cfg["auto_block"] = True
    if getattr(args, "all", False):
        cfg["full_history"] = True
    return cfg


def validate_cfg(cfg: Dict[str, Any]) -> None:
    tier = str(cfg.get("tier") or "standard")
    if tier not in TIER_FLAGS:
        raise SystemExit("config.tier=%r 不认识，可选：cup / standard / kill" % tier)
    for key, low, high in (("threshold", 1, 1000), ("max_pages", 1, 200),
                           ("page_size", 1, 20), ("interval_seconds", 20, 86400),
                           ("sub_reply_pages", 1, 50), ("lookback_hours", 1, 24 * 365)):
        try:
            val = int(cfg.get(key))
        except (TypeError, ValueError):
            raise SystemExit("config.%s 必须是整数，当前是 %r" % (key, cfg.get(key)))
        if not (low <= val <= high):
            raise SystemExit("config.%s=%s 超出合理范围 %s~%s" % (key, val, low, high))
    if cfg.get("report") and int(cfg.get("report_reason") or 0) not in REPORT_REASONS:
        warn("report_reason=%r 不在已知枚举里，举报可能返回参数错误。" % cfg.get("report_reason"))


def cmd_check(args: argparse.Namespace) -> int:
    cfg = _apply_overrides(load_config(args.config), args)
    validate_cfg(cfg)
    cfg["dry_run"] = True
    import tempfile
    tmp_dir = tempfile.gettempdir()
    tmp_state = os.path.join(tmp_dir, "fxxk_off_dfy_check_state_%d.json" % os.getpid())
    tmp_log = os.path.join(tmp_dir, "fxxk_off_dfy_check_events_%d.jsonl" % os.getpid())
    # 体检不能污染生产 state / events：events.jsonl 是事后恢复的唯一依据
    cfg["state_file"] = tmp_state
    cfg["log_file"] = tmp_log
    guard = Guard(cfg, dry_run=True)
    try:
        guard.api.http.preflight()
        me = guard.api.whoami()
        guard.up_mid = int(me["mid"])
        print("体检模式：只打印命中结果，不会删除任何评论。账号 %s(mid=%s)，档位 %s，阈值 %s"
              % (me["uname"], me["mid"], cfg.get("tier"), cfg.get("threshold")))
        guard.run_once()
        print("\n本次命中明细已写入 %s（action=would_delete），生产 events.jsonl 未被改动。" % tmp_log)
        print("确认规则没问题后，把 config.json 的 dry_run 改为 false 再运行 run。")
    finally:
        try:
            if os.path.exists(tmp_state):
                os.remove(tmp_state)
        except OSError:
            pass
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _apply_overrides(load_config(args.config), args)
    validate_cfg(cfg)
    if args.live:
        cfg["dry_run"] = False
    guard = Guard(cfg)
    guard.api.http.preflight()
    me = guard.api.whoami()
    guard.up_mid = int(me["mid"])
    mode = "DRY-RUN（只记录不删除）" if guard.dry_run else "LIVE（命中即删除）"
    print("账号 %s(mid=%s) | 模式 %s | 档位 %s | 阈值 %s | 举报 %s | 自动拉黑 %s"
          % (me["uname"], me["mid"], mode, cfg.get("tier"), cfg.get("threshold"),
             "开" if cfg.get("report") else "关", "开" if cfg.get("auto_block") else "关"))
    guard.run_loop(once=args.once)
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    print("规则引擎自测（离线，不联网）\n")
    return selftest()


def cmd_stats(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    state = State(cfg.get("state_file") or DEFAULT_STATE)
    print("累计统计：%s" % json.dumps(state.data.get("stats", {}), ensure_ascii=False))
    print("已拉黑 %d 个账号。" % len(state.data.get("blocked_mids", {})))
    for bvid, rec in list(state.data.get("videos", {}).items())[:20]:
        print("  %-14s 处理 %5d 条，最后时间 %s，标题 %s" % (
            bvid, len(rec.get("handled", {})), fmt_time(rec.get("last_seen_ctime")),
            truncate(rec.get("title") or "", 30)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fxxk_off_dfy.py",
        description="B站「大肥鱼 / AI 机器人」评论清理器：监控自己视频的评论，命中即删（可选举报、拉黑）。",
        epilog="先 check 后 run。第一次务必保持 dry_run=true。",
    )
    p.add_argument("--config", default=DEFAULT_CONFIG, help="配置文件路径（默认 config.json）")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("init", help="生成 config.json")
    s.add_argument("--cookie", default="", help="直接传入 Cookie 字符串")
    s.add_argument("--force", action="store_true", help="覆盖已存在的 config.json")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("whoami", help="校验 Cookie、列出最近投稿")
    s.set_defaults(func=cmd_whoami)

    s = sub.add_parser("check", help="体检：只打印命中，不删除")
    s.add_argument("--video", nargs="*", help="只检查这些 bvid")
    s.add_argument("--tier", choices=["cup", "standard", "kill"], help="规则档位")
    s.add_argument("--threshold", type=int, help="软信号阈值")
    s.add_argument("--all", action="store_true", help="忽略回溯窗口，扫全部可见评论")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("run", help="守护循环，正式清理")
    s.add_argument("--once", action="store_true", help="只跑一轮就退出")
    s.add_argument("--live", action="store_true", help="强制关闭 dry-run，真的删除")
    s.add_argument("--video", nargs="*", help="只处理这些 bvid")
    s.add_argument("--tier", choices=["cup", "standard", "kill"], help="规则档位")
    s.add_argument("--threshold", type=int, help="软信号阈值")
    s.add_argument("--interval", type=int, help="轮询间隔秒数")
    s.add_argument("--report", action="store_true", help="命中后一并举报")
    s.add_argument("--block", action="store_true", help="同一账号反复命中后拉黑")
    s.add_argument("--all", action="store_true", help="忽略回溯窗口，扫全部可见评论")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("stats", help="查看累计统计与已处理记录")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("selftest", help="离线跑规则引擎自测")
    s.set_defaults(func=cmd_selftest)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    print("fxxk-off-dfy v%s" % VERSION)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
