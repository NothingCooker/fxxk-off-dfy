#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fxxk_off_dfy_gui.py —— fxxk-off-dfy 的图形界面（Tkinter，标准库，零依赖）

双击 `4-gui.bat` 或运行：
    python fxxk_off_dfy_gui.py

界面上能做的：
    · 粘贴/校验 Cookie，显示账号信息
    · 调档位、阈值、回溯窗口、轮询间隔
    · 一键刷新自己的投稿列表并勾选要监控的视频
    · 「体检」= dry-run 跑一轮，只打印不删（用临时 state/events，不污染审计日志）
    · 「开始守护」按间隔轮询，命中即处置；随时停止
    · 实时日志 + 本轮统计
"""

from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional

import fxxk_off_dfy as core

APP_TITLE = "fxxk-off-dfy  ·  B站大肥鱼评论清理器"
CONFIG_PATH = core.DEFAULT_CONFIG

# 配色（B站粉 + 中性灰）
BG = "#f6f4f2"
CARD = "#ffffff"
ACCENT = "#fb7299"
ACCENT_DARK = "#e35b85"
TEXT = "#1f1f23"
DIM = "#83838d"
BORDER = "#e4e0dc"
OK_GREEN = "#16a34a"
WARN_AMBER = "#b45309"
ERR_RED = "#dc2626"
PURPLE = "#7c3aed"
GREY = "#6b7280"

FONT_UI = ("Microsoft YaHei UI", 10)
FONT_SMALL = ("Microsoft YaHei UI", 9)
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 13, "bold")
FONT_MONO = ("Consolas", 9)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1000x780")
        self.minsize(900, 680)
        self.configure(bg=BG)

        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.stop_event: Optional[threading.Event] = None
        self.running = False
        self.busy = False
        self.me: Dict[str, Any] = {}
        self.video_rows: Dict[str, Dict[str, Any]] = {}
        self.video_checked: Dict[str, bool] = {}
        self.last_totals: Dict[str, int] = {}

        self.cfg = self._load_cfg()
        self._build_style()
        self._build_ui()
        self._fill_from_cfg()

        core.set_log_sink(self._log_sink, quiet=True)
        self.after(120, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._append_log("info", "界面已就绪。先填 Cookie → 校验 → 刷新投稿 → 体检 → 开始守护。")

    # =====================================================================
    # 配置
    # =====================================================================
    def _load_cfg(self) -> Dict[str, Any]:
        if os.path.exists(CONFIG_PATH):
            try:
                return core.load_config(CONFIG_PATH)
            except SystemExit as e:
                messagebox.showwarning("配置读取失败", str(e))
        return core.merge_config({})

    def _collect_cfg(self) -> Dict[str, Any]:
        cfg = json.loads(json.dumps({k: v for k, v in self.cfg.items() if k != "_comment"}))
        cfg["_comment"] = "图形界面生成。Cookie 等价于账号密码，别提交到 git、别外发。"
        cfg["cookie"] = self.var_cookie.get().strip()
        cfg["tier"] = self.var_tier.get()
        cfg["threshold"] = int(float(self.var_threshold.get()))
        cfg["lookback_hours"] = int(self.var_lookback.get())
        cfg["max_pages"] = int(self.var_max_pages.get())
        cfg["interval_seconds"] = int(self.var_interval.get())
        cfg["page_size"] = 20
        cfg["dry_run"] = bool(self.var_dry.get())
        cfg["report"] = bool(self.var_report.get())
        cfg["report_reason"] = int(self.var_reason.get().split()[0])
        cfg["auto_block"] = bool(self.var_block.get())
        cfg["block_after_hits"] = int(self.var_block_hits.get())
        cfg["skip_pinned"] = bool(self.var_skip_pinned.get())
        cfg["skip_self"] = bool(self.var_skip_self.get())
        cfg["include_sub_replies"] = bool(self.var_subs.get())
        cfg["auto_discover"] = bool(self.var_auto.get())
        cfg["videos"] = self._checked_bvids()
        words = [w.strip() for w in self.var_words.get().split(",") if w.strip()]
        cfg["custom_words"] = words
        cfg["state_file"] = os.path.join(os.path.dirname(CONFIG_PATH), "state.json")
        cfg["log_file"] = os.path.join(os.path.dirname(CONFIG_PATH), "events.jsonl")
        return cfg

    def _save_cfg(self, silent: bool = False) -> bool:
        try:
            cfg = self._collect_cfg()
            core.validate_cfg(cfg)
        except SystemExit as e:
            messagebox.showerror("配置有误", str(e))
            return False
        self.cfg = cfg
        try:
            core.save_json(CONFIG_PATH, cfg)
        except OSError as e:
            messagebox.showerror("保存失败", str(e))
            return False
        if not silent:
            self._append_log("info", "配置已保存到 %s" % CONFIG_PATH)
        return True

    def _checked_bvids(self) -> List[str]:
        return [b for b, ok in self.video_checked.items() if ok]

    # =====================================================================
    # 样式与布局
    # =====================================================================
    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=TEXT, font=FONT_UI)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD, relief="flat")
        style.configure("TLabel", background=BG, foreground=TEXT, font=FONT_UI)
        style.configure("Card.TLabel", background=CARD, foreground=TEXT, font=FONT_UI)
        style.configure("Dim.TLabel", background=CARD, foreground=DIM, font=FONT_SMALL)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=FONT_TITLE)
        style.configure("TLabelframe", background=CARD, bordercolor=BORDER, relief="solid")
        style.configure("TLabelframe.Label", background=CARD, foreground=DIM, font=FONT_BOLD)
        style.configure("TCheckbutton", background=CARD, foreground=TEXT, font=FONT_UI)
        style.configure("TRadiobutton", background=CARD, foreground=TEXT, font=FONT_UI)
        style.configure("TButton", font=FONT_UI, padding=(10, 6))
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                        font=FONT_BOLD, padding=(14, 8), borderwidth=0)
        style.map("Accent.TButton",
                  background=[("active", ACCENT_DARK), ("disabled", "#f3c3d1")],
                  foreground=[("disabled", "#ffffff")])
        style.configure("Ghost.TButton", background=CARD, foreground=TEXT, padding=(10, 6))
        style.map("Ghost.TButton", background=[("active", "#f0ece9")])
        style.configure("Stop.TButton", background="#fee2e2", foreground=ERR_RED,
                        font=FONT_BOLD, padding=(14, 8))
        style.map("Stop.TButton", background=[("active", "#fecaca"), ("disabled", "#f5f5f4")],
                  foreground=[("disabled", "#c9c9c9")])
        style.configure("TEntry", fieldbackground="#ffffff", bordercolor=BORDER, padding=4)
        style.configure("TSpinbox", fieldbackground="#ffffff", bordercolor=BORDER, padding=3)
        style.configure("TCombobox", fieldbackground="#ffffff", bordercolor=BORDER, padding=3)
        style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                        rowheight=26, font=FONT_UI, borderwidth=0)
        style.configure("Treeview.Heading", font=FONT_BOLD, background="#faf8f6", foreground=DIM)
        style.configure("TScale", background=CARD)
        style.configure("Status.TLabel", background="#f0ece9", foreground=DIM, font=FONT_SMALL)

    def _card(self, parent: tk.Widget, **kw: Any) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Card.TFrame", padding=12)
        frame.pack(fill="x", padx=14, pady=(0, 10), **kw)
        return frame

    def _build_ui(self) -> None:
        # ---------- 顶部 ----------
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(header, text="fxxk-off-dfy", bg=BG, fg=TEXT, font=("Microsoft YaHei UI", 16, "bold")).pack(side="left")
        tk.Label(header, text="  服务端清理 B 站「大肥鱼」机器人评论", bg=BG, fg=DIM, font=FONT_SMALL).pack(side="left", pady=(6, 0))
        self.lbl_mode = tk.Label(header, text="DRY-RUN", bg="#fde7ee", fg=ACCENT_DARK,
                                 font=FONT_BOLD, padx=10, pady=3)
        self.lbl_mode.pack(side="right")
        self.lbl_state = tk.Label(header, text="● 未运行", bg=BG, fg=DIM, font=FONT_BOLD)
        self.lbl_state.pack(side="right", padx=(0, 12))

        # ---------- 账号 ----------
        card = self._card(self)
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x")
        tk.Label(row, text="Cookie", bg=CARD, fg=TEXT, font=FONT_BOLD, width=8, anchor="w").pack(side="left")
        self.var_cookie = tk.StringVar()
        self.entry_cookie = ttk.Entry(row, textvariable=self.var_cookie, show="•")
        self.entry_cookie.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(row, text="显示", style="Ghost.TButton", width=5,
                   command=self._toggle_cookie).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="校验登录", style="Ghost.TButton",
                   command=self._on_login).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="获取方法", style="Ghost.TButton",
                   command=self._show_cookie_help).pack(side="left")

        info = tk.Frame(card, bg=CARD)
        info.pack(fill="x", pady=(8, 0))
        self.lbl_account = tk.Label(info, text="未校验", bg=CARD, fg=DIM, font=FONT_SMALL, anchor="w")
        self.lbl_account.pack(side="left")
        ttk.Button(info, text="刷新投稿列表", style="Ghost.TButton",
                   command=self._on_refresh_videos).pack(side="right")

        # ---------- 规则 ----------
        rules = ttk.LabelFrame(self, text=" 规则 ")
        rules.pack(fill="x", padx=14, pady=(0, 10))
        r1 = tk.Frame(rules, bg=CARD)
        r1.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(r1, text="档位", bg=CARD, fg=TEXT, font=FONT_BOLD).pack(side="left")
        self.var_tier = tk.StringVar(value="standard")
        for val, label in (("cup", "小杯"), ("standard", "标准"), ("kill", "杀戮")):
            ttk.Radiobutton(r1, text=label, value=val, variable=self.var_tier,
                            command=self._on_tier).pack(side="left", padx=(8, 0))
        tk.Label(r1, text="   阈值", bg=CARD, fg=TEXT, font=FONT_BOLD).pack(side="left")
        self.var_threshold = tk.DoubleVar(value=60)
        ttk.Scale(r1, from_=10, to=100, variable=self.var_threshold, length=160,
                  command=lambda _v: self.lbl_threshold.config(text=str(int(float(self.var_threshold.get()))))
                  ).pack(side="left", padx=(6, 4))
        self.lbl_threshold = tk.Label(r1, text="60", bg=CARD, fg=DIM, font=FONT_SMALL, width=3)
        self.lbl_threshold.pack(side="left")
        tk.Label(r1, text="回溯", bg=CARD, fg=TEXT, font=FONT_BOLD).pack(side="left", padx=(14, 4))
        self.var_lookback = tk.StringVar(value="72")
        ttk.Spinbox(r1, from_=1, to=8760, width=6, textvariable=self.var_lookback).pack(side="left")
        tk.Label(r1, text="小时", bg=CARD, fg=DIM, font=FONT_SMALL).pack(side="left", padx=(4, 0))

        r2 = tk.Frame(rules, bg=CARD)
        r2.pack(fill="x", padx=10, pady=(2, 8))
        self.var_dry = tk.BooleanVar(value=True)
        ttk.Checkbutton(r2, text="体检模式（只记录不删除）", variable=self.var_dry,
                        command=self._sync_mode_badge).pack(side="left")
        self.var_report = tk.BooleanVar(value=False)
        ttk.Checkbutton(r2, text="举报", variable=self.var_report).pack(side="left", padx=(14, 4))
        self.var_reason = tk.StringVar(value="8 垃圾信息/刷屏")
        ttk.Combobox(r2, textvariable=self.var_reason, width=16, state="readonly",
                     values=["%d %s" % (k, v) for k, v in sorted(core.REPORT_REASONS.items())]
                     ).pack(side="left")
        self.var_block = tk.BooleanVar(value=False)
        ttk.Checkbutton(r2, text="拉黑", variable=self.var_block).pack(side="left", padx=(14, 4))
        self.var_block_hits = tk.StringVar(value="3")
        ttk.Spinbox(r2, from_=1, to=50, width=4, textvariable=self.var_block_hits).pack(side="left")
        tk.Label(r2, text="次后", bg=CARD, fg=DIM, font=FONT_SMALL).pack(side="left", padx=(4, 0))

        r3 = tk.Frame(rules, bg=CARD)
        r3.pack(fill="x", padx=10, pady=(0, 10))
        self.var_skip_pinned = tk.BooleanVar(value=True)
        ttk.Checkbutton(r3, text="跳过置顶", variable=self.var_skip_pinned).pack(side="left")
        self.var_skip_self = tk.BooleanVar(value=True)
        ttk.Checkbutton(r3, text="跳过自己的评论", variable=self.var_skip_self).pack(side="left", padx=(14, 0))
        self.var_subs = tk.BooleanVar(value=True)
        ttk.Checkbutton(r3, text="扫楼中楼", variable=self.var_subs).pack(side="left", padx=(14, 0))
        tk.Label(r3, text="自定义屏蔽词", bg=CARD, fg=TEXT, font=FONT_BOLD).pack(side="left", padx=(18, 6))
        self.var_words = tk.StringVar()
        ttk.Entry(r3, textvariable=self.var_words).pack(side="left", fill="x", expand=True)
        tk.Label(r3, text="逗号分隔", bg=CARD, fg=DIM, font=FONT_SMALL).pack(side="left", padx=(6, 0))

        # ---------- 视频 ----------
        vids = ttk.LabelFrame(self, text=" 监控对象 ")
        vids.pack(fill="both", expand=False, padx=14, pady=(0, 10))
        vh = tk.Frame(vids, bg=CARD)
        vh.pack(fill="x", padx=10, pady=(8, 4))
        self.var_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(vh, text="自动监控全部投稿（含新发布的）", variable=self.var_auto,
                        command=self._on_auto_toggle).pack(side="left")
        tk.Label(vh, text="  每轮最多", bg=CARD, fg=DIM, font=FONT_SMALL).pack(side="left", padx=(12, 4))
        self.var_max_pages = tk.StringVar(value="8")
        ttk.Spinbox(vh, from_=1, to=60, width=4, textvariable=self.var_max_pages).pack(side="left")
        tk.Label(vh, text="页   轮询间隔", bg=CARD, fg=DIM, font=FONT_SMALL).pack(side="left", padx=(4, 4))
        self.var_interval = tk.StringVar(value="120")
        ttk.Spinbox(vh, from_=20, to=3600, width=6, textvariable=self.var_interval).pack(side="left")
        tk.Label(vh, text="秒", bg=CARD, fg=DIM, font=FONT_SMALL).pack(side="left", padx=(4, 0))

        tree_wrap = tk.Frame(vids, bg=CARD)
        tree_wrap.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        columns = ("bvid", "title", "comment")
        self.tree = ttk.Treeview(tree_wrap, columns=columns, show="headings", height=5)
        self.tree.heading("bvid", text="BV 号")
        self.tree.heading("title", text="标题")
        self.tree.heading("comment", text="评论数")
        self.tree.column("bvid", width=130, anchor="w", stretch=False)
        self.tree.column("title", width=600, anchor="w")
        self.tree.column("comment", width=80, anchor="e", stretch=False)
        sb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_tree_click)

        # ---------- 操作区 ----------
        ops = tk.Frame(self, bg=BG)
        ops.pack(fill="x", padx=14, pady=(0, 8))
        self.btn_check = ttk.Button(ops, text="体检（只打印，不删）", style="Accent.TButton",
                                    command=lambda: self._on_run(dry=True))
        self.btn_check.pack(side="left")
        self.btn_start = ttk.Button(ops, text="开始守护", style="Accent.TButton", command=self._on_start)
        self.btn_start.pack(side="left", padx=(8, 0))
        self.btn_stop = ttk.Button(ops, text="停止", style="Stop.TButton", command=self._on_stop,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=(8, 0))
        ttk.Button(ops, text="保存配置", style="Ghost.TButton",
                   command=lambda: self._save_cfg()).pack(side="left", padx=(8, 0))
        ttk.Button(ops, text="打开审计日志", style="Ghost.TButton",
                   command=self._open_log).pack(side="left", padx=(8, 0))
        ttk.Button(ops, text="规则自测", style="Ghost.TButton",
                   command=self._on_selftest).pack(side="left", padx=(8, 0))

        # ---------- 日志 ----------
        log_wrap = tk.Frame(self, bg=BG)
        log_wrap.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        self.txt = tk.Text(log_wrap, bg="#1f1f23", fg="#e7e5e4", font=FONT_MONO,
                           relief="flat", wrap="word", padx=10, pady=8, height=14)
        lsb = ttk.Scrollbar(log_wrap, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=lsb.set)
        self.txt.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")
        self.txt.tag_configure("info", foreground="#d6d3d1")
        self.txt.tag_configure("warn", foreground="#fbbf24")
        self.txt.tag_configure("del", foreground="#f87171")
        self.txt.tag_configure("dry", foreground="#a8a29e")
        self.txt.tag_configure("block", foreground="#c4b5fd")
        self.txt.tag_configure("ok", foreground="#86efac")
        self.txt.configure(state="disabled")

        # ---------- 状态栏 ----------
        bar = tk.Frame(self, bg="#f0ece9", height=28)
        bar.pack(fill="x", side="bottom")
        self.lbl_stats = tk.Label(bar, text="本轮：扫描 0 / 命中 0 / 删除 0 / 举报 0 / 拉黑 0 / 失败 0",
                                  bg="#f0ece9", fg=DIM, font=FONT_SMALL, anchor="w")
        self.lbl_stats.pack(side="left", padx=12, pady=5)
        self.lbl_tip = tk.Label(bar, text="", bg="#f0ece9", fg=DIM, font=FONT_SMALL, anchor="e")
        self.lbl_tip.pack(side="right", padx=12, pady=5)

    # =====================================================================
    # 填充/同步
    # =====================================================================
    def _fill_from_cfg(self) -> None:
        cfg = self.cfg
        self.var_cookie.set(str(cfg.get("cookie") or ""))
        self.var_tier.set(str(cfg.get("tier") or "standard"))
        self.var_threshold.set(float(cfg.get("threshold") or 60))
        self.lbl_threshold.config(text=str(int(float(self.var_threshold.get()))))
        self.var_lookback.set(str(cfg.get("lookback_hours") or 72))
        self.var_max_pages.set(str(cfg.get("max_pages") or 8))
        self.var_interval.set(str(cfg.get("interval_seconds") or 120))
        self.var_dry.set(not core.is_live_flag(cfg.get("dry_run", True)))
        self.var_report.set(bool(cfg.get("report")))
        reason = int(cfg.get("report_reason") or core.DEFAULT_REPORT_REASON)
        self.var_reason.set("%d %s" % (reason, core.REPORT_REASONS.get(reason, "")))
        self.var_block.set(bool(cfg.get("auto_block")))
        self.var_block_hits.set(str(cfg.get("block_after_hits") or 3))
        self.var_skip_pinned.set(bool(cfg.get("skip_pinned", True)))
        self.var_skip_self.set(bool(cfg.get("skip_self", True)))
        self.var_subs.set(bool(cfg.get("include_sub_replies", True)))
        self.var_auto.set(bool(cfg.get("auto_discover", True)))
        self.var_words.set(",".join(str(w) for w in (cfg.get("custom_words") or [])))
        self._sync_mode_badge()
        self._on_auto_toggle()
        for bvid in core.as_list(cfg.get("videos")):
            key = str(bvid)
            if key and key not in self.video_checked:
                self.video_checked[key] = True
                self.video_rows[key] = {"bvid": key, "title": "(手动添加)", "comment": ""}
                self._insert_tree_row(key)

    def _sync_mode_badge(self) -> None:
        if self.var_dry.get():
            self.lbl_mode.config(text="DRY-RUN", bg="#fde7ee", fg=ACCENT_DARK)
        else:
            self.lbl_mode.config(text="LIVE 会真删", bg="#fee2e2", fg=ERR_RED)

    def _on_tier(self) -> None:
        tier = self.var_tier.get()
        if tier == "kill":
            self.var_threshold.set(70)
        elif tier == "cup":
            self.var_threshold.set(80)
        else:
            self.var_threshold.set(60)
        self.lbl_threshold.config(text=str(int(float(self.var_threshold.get()))))

    def _on_auto_toggle(self) -> None:
        # 自动监控全部时，列表只作展示，不允许改勾选
        state = "disabled" if self.var_auto.get() else "normal"
        self.lbl_tip.config(text="自动模式下会处理全部投稿，列表仅展示" if self.var_auto.get() else "")
        self._auto_mode = state == "disabled"

    def _toggle_cookie(self) -> None:
        self.entry_cookie.config(show="" if self.entry_cookie.cget("show") else "•")

    # =====================================================================
    # 日志
    # =====================================================================
    def _log_sink(self, level: str, text: str) -> None:
        self.q.put(("log", level, text))

    def _append_log(self, level: str, text: str) -> None:
        tag = "info"
        if level == "warn":
            tag = "warn"
        elif "已删" in text:
            tag = "del"
        elif "[DRY]" in text:
            tag = "dry"
        elif "已拉黑" in text:
            tag = "block"
        elif "已停止" in text or "已保存" in text or "就绪" in text:
            tag = "ok"
        self.txt.configure(state="normal")
        self.txt.insert("end", text + "\n", tag)
        # 控制总行数
        lines = int(self.txt.index("end-1c").split(".")[0])
        if lines > 1200:
            self.txt.delete("1.0", "%d.0" % (lines - 1000))
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _open_log(self) -> None:
        path = self._collect_cfg()["log_file"]
        if not os.path.exists(path):
            messagebox.showinfo("还没有日志", "还没有产生审计日志。\n跑一次体检或守护后就有了。")
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as e:
            messagebox.showinfo("日志路径", "%s\n\n%s" % (path, e))

    # =====================================================================
    # 后台任务
    # =====================================================================
    def _run_bg(self, fn, on_done=None) -> None:
        if self.busy:
            messagebox.showinfo("稍等", "上一步还没结束。")
            return
        self.busy = True
        self._set_buttons()

        def target() -> None:
            try:
                res = fn()
                self.q.put(("done", on_done, res, None))
            except SystemExit as e:
                self.q.put(("done", on_done, None, str(e) or "已退出"))
            except BaseException as e:  # noqa: BLE001
                self.q.put(("done", on_done, None, "%s: %s" % (type(e).__name__, e)))

        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _set_buttons(self) -> None:
        state = "disabled" if (self.busy or self.running) else "normal"
        self.btn_check.config(state=state)
        self.btn_start.config(state=state)
        self.btn_stop.config(state="normal" if self.running else "disabled")

    def _drain_queue(self) -> None:
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._append_log(msg[1], msg[2])
                elif kind == "done":
                    _, cb, res, err = msg
                    self.busy = False
                    self._set_buttons()
                    if err:
                        self._append_log("warn", "操作失败：%s" % err)
                        messagebox.showerror("失败", str(err))
                    elif cb:
                        cb(res)
                elif kind == "round":
                    self._on_round_ui(msg[1], msg[2])
                elif kind == "thread_end":
                    self.running = False
                    self.lbl_state.config(text="● 已停止", fg=DIM)
                    self._set_buttons()
        except queue.Empty:
            pass
        self.after(120, self._drain_queue)

    def _on_round(self, totals: Dict[str, int], error: Optional[str]) -> None:
        self.q.put(("round", dict(totals), error))

    def _on_round_ui(self, totals: Dict[str, int], error: Optional[str]) -> None:
        self.lbl_stats.config(
            text="本轮：扫描 %(scanned)d / 命中 %(hit)d / 删除 %(deleted)d / 待删 %(would)d / "
                 "举报 %(reported)d / 拉黑 %(blocked)d / 失败 %(failed)d" % totals)
        if error:
            self.running = False
            self.lbl_state.config(text="● 已停止（Cookie 失效）", fg=ERR_RED)
            self._set_buttons()
            messagebox.showerror("运行中止", error)

    # =====================================================================
    # 动作
    # =====================================================================
    def _on_login(self) -> None:
        if not self._save_cfg(silent=True):
            return
        cfg = self._collect_cfg()

        def work():
            api = core.BiliAPI(cfg)
            api.http.preflight()
            return api.whoami()

        def done(me):
            me = me or {}
            self.me = me
            self.lbl_account.config(
                text="已登录：%s（mid=%s，Lv%s）" % (me.get("uname"), me.get("mid"), me.get("level")),
                fg=OK_GREEN)
            self._append_log("info", "登录校验通过：%s (mid=%s)" % (me.get("uname"), me.get("mid")))

        self._run_bg(work, done)

    def _show_cookie_help(self) -> None:
        text = (
            "怎么拿 Cookie（一次就够，SESSDATA 有效期很长）：\n\n"
            "1. Chrome / Edge 打开 https://www.bilibili.com 并确认已登录\n"
            "2. 按 F12 → Network（网络）→ 刷新页面\n"
            "3. 点任意一个 api.bilibili.com 请求 → Request Headers\n"
            "4. 找到 Cookie: 整行复制（很长，全部要）\n"
            "5. 粘贴到本窗口的 Cookie 输入框 → 点「校验登录」\n\n"
            "必须包含：SESSDATA、bili_jct、DedeUserID\n"
            "建议一并带上：buvid3、b_nut、bili_ticket（缺了更容易吃 -352 风控）\n\n"
            "注意：Cookie 等价于账号密码。config.json 不要提交到 git、不要发群里。"
        )
        win = tk.Toplevel(self)
        win.title("如何获取 Cookie")
        win.configure(bg=CARD)
        win.geometry("560x360")
        tk.Label(win, text=text, bg=CARD, fg=TEXT, font=FONT_UI, justify="left",
                 anchor="nw", padx=18, pady=16).pack(fill="both", expand=True)

    def _on_refresh_videos(self) -> None:
        if not self._save_cfg(silent=True):
            return
        cfg = self._collect_cfg()
        if not cfg.get("cookie"):
            messagebox.showwarning("先填 Cookie", "刷新投稿列表需要登录态。")
            return

        def work():
            api = core.BiliAPI(cfg)
            me = api.whoami()
            vids = api.list_own_videos(int(me["mid"]), pages=3)
            return me, vids

        def done(res):
            me, vids = res
            self.me = me
            self.lbl_account.config(
                text="已登录：%s（mid=%s，Lv%s）" % (me.get("uname"), me.get("mid"), me.get("level")),
                fg=OK_GREEN)
            for bvid in list(self.video_rows.keys()):
                self.tree.delete(bvid)
            self.video_rows.clear()
            self.video_checked.clear()
            for v in vids:
                bvid = v["bvid"]
                self.video_rows[bvid] = v
                self.video_checked[bvid] = True
                self._insert_tree_row(bvid)
            self._append_log("info", "已刷新投稿：%d 个" % len(vids))

        self._run_bg(work, done)

    def _insert_tree_row(self, bvid: str) -> None:
        if self.tree.exists(bvid):
            return
        v = self.video_rows.get(bvid) or {}
        mark = "☑" if self.video_checked.get(bvid) else "☐"
        self.tree.insert("", "end", iid=bvid,
                         values=("%s %s" % (mark, bvid), v.get("title") or "", v.get("comment") or ""))

    def _on_tree_click(self, event) -> None:
        if getattr(self, "_auto_mode", False):
            return
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self.video_checked[item] = not self.video_checked.get(item, True)
        v = self.video_rows.get(item) or {}
        mark = "☑" if self.video_checked[item] else "☐"
        self.tree.item(item, values=("%s %s" % (mark, item), v.get("title") or "", v.get("comment") or ""))

    # -- 体检 / 守护 ------------------------------------------------------
    def _prepare_guard(self, dry_run: bool, cfg: Dict[str, Any]):
        if dry_run:
            tmp = tempfile.mkdtemp(prefix="dfy_gui_check_")
            cfg = dict(cfg)
            cfg["state_file"] = os.path.join(tmp, "state.json")
            cfg["log_file"] = os.path.join(tmp, "events.jsonl")
        guard = core.Guard(cfg, dry_run=dry_run, stop_event=self.stop_event,
                           on_round=self._on_round)
        return guard

    def _on_run(self, dry: bool) -> None:
        if not self._save_cfg(silent=True):
            return
        cfg = self._collect_cfg()
        if not cfg.get("cookie"):
            messagebox.showwarning("先填 Cookie", "没有 Cookie 没法读评论。点「获取方法」看看怎么拿。")
            return
        self.stop_event = threading.Event()
        self.lbl_state.config(text="● 体检中", fg=ACCENT_DARK)
        self._append_log("info", "开始体检：只打印命中，不删除。")

        def work():
            guard = self._prepare_guard(True, cfg)
            guard.api.http.preflight()
            me = guard.api.whoami()
            guard.up_mid = int(me["mid"])
            self.q.put(("log", "info", "账号 %s(mid=%s)，档位 %s，阈值 %s"
                        % (me["uname"], me["mid"], cfg.get("tier"), cfg.get("threshold"))))
            guard.run_loop(once=True)
            return guard.totals

        def done(totals):
            self.lbl_state.config(text="● 体检完成", fg=OK_GREEN)
            self._append_log("info", "体检结束。要真删的话，取消「体检模式」再点「开始守护」。")

        self._run_bg(work, done)

    def _on_start(self) -> None:
        if not self._save_cfg(silent=True):
            return
        cfg = self._collect_cfg()
        if not cfg.get("cookie"):
            messagebox.showwarning("先填 Cookie", "没有 Cookie 没法读评论。")
            return
        live = not cfg.get("dry_run")
        if live and not messagebox.askyesno(
                "确认开启真删？",
                "当前是 LIVE 模式：命中规则的评论会被真的删除，无法恢复。\n\n"
                "档位：%s    阈值：%s\n举报：%s    自动拉黑：%s\n\n确定开始？"
                % (cfg.get("tier"), cfg.get("threshold"),
                   "开" if cfg.get("report") else "关",
                   "开" if cfg.get("auto_block") else "关")):
            return
        self.stop_event = threading.Event()
        self.running = True
        self.busy = False
        self._set_buttons()
        self.lbl_state.config(text="● 运行中（%s）" % ("LIVE" if live else "DRY-RUN"), fg=OK_GREEN)
        self.btn_check.config(state="disabled")
        self.btn_start.config(state="disabled")
        self._append_log("info", "开始守护：%s 模式，间隔 %s 秒"
                         % ("LIVE（会真删）" if live else "DRY-RUN（不删）", cfg.get("interval_seconds")))

        def work():
            try:
                guard = self._prepare_guard(not live, cfg)
                guard.api.http.preflight()
                me = guard.api.whoami()
                guard.up_mid = int(me["mid"])
                guard.run_loop()
            except SystemExit as e:
                self.q.put(("log", "warn", str(e)))
            except BaseException as e:  # noqa: BLE001
                self.q.put(("log", "warn", "守护线程异常：%s: %s" % (type(e).__name__, e)))
            finally:
                self.q.put(("thread_end",))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _on_stop(self) -> None:
        if self.stop_event:
            self.stop_event.set()
        self._append_log("info", "正在停止……（等当前请求结束）")
        self.lbl_state.config(text="● 停止中", fg=WARN_AMBER)
        self.btn_stop.config(state="disabled")

    def _on_selftest(self) -> None:
        self._append_log("info", "开始离线规则自测（不需要 Cookie）……")
        self._run_bg(lambda: core.selftest(),
                     lambda rc: self._append_log("info", "自测结束：%s" % ("全部通过" if rc == 0 else "有用例失败")))

    def _on_close(self) -> None:
        if self.running and not messagebox.askyesno("退出", "守护正在运行，退出前会先停止。继续？"):
            return
        if self.stop_event:
            self.stop_event.set()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=2)
        self.destroy()


def main() -> int:
    core.ensure_utf8_stdio()
    try:
        app = App()
    except tk.TclError as e:
        print("无法启动图形界面：%s" % e)
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
