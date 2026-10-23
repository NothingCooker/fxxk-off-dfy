#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线集成测试：用假接口跑完整的「拉评论 → 判定 → 处置 → 落盘」流程。
不联网、不需要 Cookie。运行：

    python tests/test_offline.py
    python -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fxxk_off_dfy as g  # noqa: E402

# 用「刚刚」的时间，否则会被 lookback_hours 的窗口过滤掉
NOW = g.now_ts()


def reply(rpid, mid, uname, message, ctime=None, rcount=0, top=False, level=4):
    return {
        "rpid": rpid, "mid": mid, "ctime": ctime if ctime is not None else NOW - 60,
        "like": 0, "rcount": rcount,
        "content": {"message": message},
        "member": {"uname": uname, "level_info": {"current_level": level}},
        "reply_control": {"is_up_top": top},
    }


def page(replies, is_end=True):
    return {"replies": replies, "top": {}, "count": len(replies), "acount": len(replies),
            "is_end": is_end, "next_offset": ""}


class FakeAPI:
    """只实现 Guard 用到的接口，记录每个处置动作的顺序。"""

    def __init__(self, main_pages=None, sub_pages=None, delete_error=None):
        self.main_pages = main_pages or []
        self.sub_pages = sub_pages or {}
        self.delete_error = delete_error
        self.ops = []          # [(action, rpid/mid)]
        self.deleted = []
        self.reported = []
        self.blocked = []

    def video_info(self, bvid):
        return {"bvid": bvid, "aid": 424242, "title": "测试视频",
                "owner_mid": 999, "reply": 0, "ctime": 0}

    def list_own_videos(self, mid, pages=2):
        return []

    def fetch_main_page(self, aid, pn=1, ps=20, sort=0):
        if 1 <= pn <= len(self.main_pages):
            return self.main_pages[pn - 1]
        return page([])

    def fetch_main_page_wbi(self, aid, offset="", ps=20, mode=3):
        return page([])

    def fetch_sub_page(self, aid, root, pn=1, ps=20):
        pages = self.sub_pages.get(root) or []
        if 1 <= pn <= len(pages):
            return pages[pn - 1]
        return {"replies": [], "count": 0, "is_end": True}

    def delete_reply(self, aid, rpid):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(rpid)
        self.ops.append(("delete", rpid))

    def report_reply(self, aid, rpid, reason):
        self.reported.append((rpid, reason))
        self.ops.append(("report", rpid))

    def block_user(self, mid):
        self.blocked.append(mid)
        self.ops.append(("block", mid))


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dfy_test_")
        self.cfg = g.merge_config({
            "videos": ["BV1xx411c7mD"],
            "auto_discover": False,
            "mid": 999,
            "dry_run": True,
            "request_min_delay": 0,
            "request_max_delay": 0,
            "state_file": os.path.join(self.tmp, "state.json"),
            "log_file": os.path.join(self.tmp, "events.jsonl"),
        })

    def tearDown(self):
        for name in os.listdir(self.tmp):
            try:
                os.remove(os.path.join(self.tmp, name))
            except OSError:
                pass
        try:
            os.rmdir(self.tmp)
        except OSError:
            pass

    def make_guard(self, api, **overrides):
        cfg = json.loads(json.dumps(self.cfg))
        cfg.update(overrides)
        return g.Guard(cfg, api=api, dry_run=cfg["dry_run"])

    # ---- 1. dry-run 不删 ------------------------------------------------
    def test_dry_run_never_deletes(self):
        api = FakeAPI(main_pages=[page([
            reply(1, 5001, "旅行者的大肥鱼", "这期视频总结得真好，学到了"),
            reply(2, 5002, "老王", "三连了，下期想看这个系列的续集"),
        ])])
        guard = self.make_guard(api, dry_run=True)
        totals = guard.run_once()
        self.assertEqual(api.deleted, [])
        self.assertEqual(totals["hit"], 1)
        self.assertEqual(totals["would"], 1)
        with open(self.cfg["log_file"], encoding="utf-8") as f:
            logs = [json.loads(line) for line in f.read().splitlines()]
        self.assertEqual(logs[0]["action"], "would_delete")
        self.assertEqual(logs[0]["rpid"], 1)

    # ---- 2. live 删掉，且第二轮不重复处理 -------------------------------
    def test_live_delete_and_second_run_skips(self):
        api = FakeAPI(main_pages=[page([
            reply(1, 5001, "旅行者的大肥鱼", "这期视频总结得真好"),
            reply(2, 5002, "老王", "说得好"),
        ])])
        guard = self.make_guard(api, dry_run=False)
        totals = guard.run_once()
        self.assertEqual(api.deleted, [1])
        self.assertEqual(totals["deleted"], 1)
        # 第二轮：同一条评论已经处理过
        api.main_pages = [page([reply(1, 5001, "旅行者的大肥鱼", "这期视频总结得真好")])]
        totals2 = guard.run_once()
        self.assertEqual(api.deleted, [1])
        self.assertEqual(totals2["scanned"], 0)

    # ---- 3. 楼中楼也要删 -------------------------------------------------
    def test_sub_replies_are_scanned(self):
        api = FakeAPI(
            main_pages=[page([
                reply(10, 5003, "老王", "正常主楼", rcount=2),
            ])],
            sub_pages={10: [{"replies": [
                reply(11, 5001, "深海的大肥鱼", "本鲸也这么觉得"),
                reply(12, 5004, "路人", "同意"),
            ], "count": 2, "is_end": True}]},
        )
        guard = self.make_guard(api, dry_run=False)
        guard.run_once()
        self.assertEqual(api.deleted, [11])

    # ---- 4. 评论已经没了 -> already_gone --------------------------------
    def test_already_gone(self):
        api = FakeAPI(main_pages=[page([
            reply(1, 5001, "旅行者的大肥鱼", "本鲸来了"),
        ])], delete_error=g.BiliError(-404, "评论不存在"))
        guard = self.make_guard(api, dry_run=False)
        totals = guard.run_once()
        self.assertEqual(totals["gone"], 1)
        self.assertEqual(totals["failed"], 0)

    # ---- 5. 先举报后删除 ------------------------------------------------
    def test_report_before_delete(self):
        api = FakeAPI(main_pages=[page([
            reply(1, 5001, "旅行者的大肥鱼", "本鲸来了"),
        ])])
        guard = self.make_guard(api, dry_run=False, report=True, report_reason=8)
        guard.run_once()
        self.assertEqual(api.ops, [("report", 1), ("delete", 1)])
        self.assertEqual(api.reported, [(1, 8)])

    # ---- 6. 反复命中 -> 拉黑 --------------------------------------------
    def test_auto_block_after_repeat_hits(self):
        robot = lambda rpid, text: reply(rpid, 7001, "路人甲", text)  # noqa: E731
        api = FakeAPI(main_pages=[page([
            robot(1, "首先这期视频很有意思 @某人 其次数据也对得上"),
            robot(2, "首先这期视频很有意思 @某人 其次数据也对得上"),
        ])])
        guard = self.make_guard(api, dry_run=False, auto_block=True, block_after_hits=1)
        guard.run_once()
        # 两条都是同一 mid 的总结模板 @ 评论；第二条删除后触发拉黑
        self.assertEqual(len(api.deleted), 2)
        self.assertEqual(api.blocked, [7001])

    # ---- 7. 白名单 / 置顶 不处理 ----------------------------------------
    def test_whitelist_and_pinned(self):
        api = FakeAPI(main_pages=[page([
            reply(1, 5001, "旅行者的大肥鱼", "本鲸来了", top=True),
            reply(2, 5002, "另一个大肥鱼", "本鲸来了"),
            reply(3, 5003, "路人", "正常评论"),
        ])])
        guard = self.make_guard(api, dry_run=False, whitelist_mids=[5002])
        totals = guard.run_once()
        self.assertEqual(api.deleted, [])
        self.assertEqual(totals["hit"], 0)

    # ---- 8. 回归：dry-run 之后切 live 必须还能删 ------------------------
    def test_dry_then_live_still_deletes(self):
        api = FakeAPI(main_pages=[page([
            reply(1, 5001, "旅行者的大肥鱼", "本鲸来了"),
        ])])
        guard = self.make_guard(api, dry_run=True)
        guard.run_once()
        self.assertEqual(api.deleted, [])          # dry 不删
        guard.dry_run = False                      # 切 live
        guard.run_once()
        self.assertEqual(api.deleted, [1])         # 仍然能删

    # ---- 9. 回归：删除失败要重试，不能记成已处理 ------------------------
    def test_failed_delete_is_retried(self):
        api = FakeAPI(main_pages=[page([
            reply(1, 5001, "旅行者的大肥鱼", "本鲸来了"),
        ])], delete_error=g.BiliError(-500, "服务繁忙"))
        guard = self.make_guard(api, dry_run=False)
        guard.run_once()
        self.assertEqual(guard.totals["failed"], 1)
        api.delete_error = None
        guard.run_once()
        self.assertEqual(api.deleted, [1])

    # ---- 10. 回归：超出回溯窗口的旧评论不处理 ---------------------------
    def test_outside_lookback_is_ignored(self):
        old = g.now_ts() - 30 * 24 * 3600
        api = FakeAPI(main_pages=[page([
            reply(1, 5001, "旅行者的大肥鱼", "本鲸来了", ctime=old),
        ])])
        guard = self.make_guard(api, dry_run=False, lookback_hours=1)
        totals = guard.run_once()
        self.assertEqual(api.deleted, [])
        self.assertEqual(totals["scanned"], 0)

    # ---- 11. 回归：dry_run 取值异常时必须 fail-safe 到 dry --------------
    def test_dry_run_fail_safe(self):
        for bad in (None, "", "yes", 0, 1, [], {}):
            cfg = json.loads(json.dumps(self.cfg))
            cfg["dry_run"] = bad
            guard = g.Guard(cfg, api=FakeAPI(), dry_run=None)
            self.assertTrue(guard.dry_run, "dry_run=%r 应该按 dry-run 处理" % (bad,))

    # ---- 12. 全量历史：忽略回溯窗口 -------------------------------------
    def test_full_history_ignores_lookback(self):
        old = g.now_ts() - 30 * 24 * 3600
        api = FakeAPI(main_pages=[page([
            reply(1, 5001, "旅行者的大肥鱼", "本鲸来了", ctime=old),
        ])])
        guard = self.make_guard(api, dry_run=False, lookback_hours=1, full_history=True)
        totals = guard.run_once()
        self.assertEqual(api.deleted, [1])
        self.assertEqual(totals["scanned"], 1)

    # ---- 13. 默认模式仍然遵守回溯窗口（对照上一条）-----------------------
    def test_lookback_still_respected_by_default(self):
        old = g.now_ts() - 30 * 24 * 3600
        api = FakeAPI(main_pages=[page([
            reply(1, 5001, "旅行者的大肥鱼", "本鲸来了", ctime=old),
        ])])
        guard = self.make_guard(api, dry_run=False, lookback_hours=1, full_history=False)
        totals = guard.run_once()
        self.assertEqual(api.deleted, [])
        self.assertEqual(totals["scanned"], 0)


class JudgeRegressionTest(unittest.TestCase):
    """规则引擎的回归用例（不涉及接口）。"""

    def test_cup_tier_does_not_count_soft_words(self):
        cfg = g.merge_config({"tier": "cup"})
        v = g.reply_view(reply(1, 5001, "老王", "主人抱着本喵看视频"))
        vt = g.judge(v, cfg, up_mid=999)
        self.assertEqual(vt.score, 0)
        self.assertFalse(vt.hit)

    def test_summary_alone_cannot_reach_threshold(self):
        cfg = g.merge_config({})
        v = g.reply_view(reply(1, 5001, "老王", "首先这很好其次不错再次可以最后满意"))
        vt = g.judge(v, cfg, up_mid=999)
        self.assertEqual(vt.score, 40)
        self.assertFalse(vt.hit)

    def test_soft_words_no_double_count(self):
        cfg = g.merge_config({"tier": "kill"})
        v = g.reply_view(reply(1, 5001, "老王", "本喵"))
        vt = g.judge(v, cfg, up_mid=999)
        self.assertEqual(vt.score, 40)   # 只算「本喵+40」，不再叠加「喵+22」


if __name__ == "__main__":
    unittest.main(verbosity=2)
