#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_promo.py —— 生成 fxxk-off-dfy 的 B 站宣传片（1920x1080 / 30fps / 无声）

用法：
    python promo/make_promo.py --test     # 只渲染前 2 秒，验证编码参数
    python promo/make_promo.py            # 完整渲染

依赖：Pillow、numpy、ffmpeg（脚本里写死了 BlueStacks 自带的 ffmpeg 4.4.4，
      它带 libopenh264，可以直接编码 H.264）
"""

from __future__ import annotations

import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

W, H, FPS = 1920, 1080, 30
BG = (15, 15, 18)
INK = (232, 230, 227)
DIM = (138, 138, 146)
PINK = (251, 114, 153)
GREEN = (74, 222, 128)
RED = (248, 113, 113)
AMBER = (245, 158, 11)
CARD = (26, 26, 31)
CARD2 = (32, 32, 38)

FONT_ZH = r"C:\Windows\Fonts\msyh.ttc"
FONT_ZH_B = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_MONO = r"C:\Windows\Fonts\consola.ttf"
FFMPEG = r"C:\Program Files\BlueStacks_nxt_cn\ffmpeg.exe"

_fcache = {}


def f_zh(size: int, bold: bool = False):
    key = ("zh", size, bold)
    if key not in _fcache:
        _fcache[key] = ImageFont.truetype(FONT_ZH_B if bold else FONT_ZH, size)
    return _fcache[key]


def f_en(size: int):
    key = ("en", size)
    if key not in _fcache:
        _fcache[key] = ImageFont.truetype(FONT_MONO, size)
    return _fcache[key]


def runs(text: str):
    """把字符串切成连续 ASCII / 非 ASCII 段，方便中英混排。"""
    out, cur, cur_ascii = [], "", None
    for ch in text:
        is_ascii = ord(ch) < 128
        if cur_ascii is None or is_ascii == cur_ascii:
            cur += ch
            cur_ascii = is_ascii
        else:
            out.append((cur, cur_ascii))
            cur, cur_ascii = ch, is_ascii
    if cur:
        out.append((cur, cur_ascii))
    return out


def measure(text: str, size: int, bold: bool = False) -> float:
    total = 0.0
    for seg, ascii_ in runs(text):
        f = f_en(size) if ascii_ else f_zh(size, bold)
        total += f.getlength(seg)
    return total


def draw_text(d: ImageDraw.ImageDraw, x: float, y: float, text: str, size: int,
              color, bold: bool = False, center: bool = False) -> float:
    if center:
        x -= measure(text, size, bold) / 2
    for seg, ascii_ in runs(text):
        f = f_en(size) if ascii_ else f_zh(size, bold)
        d.text((x, y), seg, font=f, fill=color)
        x += f.getlength(seg)
    return x


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def ease_out(t: float) -> float:
    t = clamp(t)
    return 1 - (1 - t) ** 3


def fade(t: float, start: float, dur: float, out_start: float = None, out_dur: float = 0.5) -> float:
    a = clamp((t - start) / dur) if dur > 0 else 1.0
    if out_start is not None:
        a *= 1 - clamp((t - out_start) / out_dur)
    return ease_out(a)


def mix(c1, c2, k: float):
    k = clamp(k)
    return tuple(int(c1[i] + (c2[i] - c1[i]) * k) for i in range(3))


def fade_in_image(img: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 1:
        return img
    out = img.copy()
    out.putalpha(int(255 * clamp(alpha)))
    return out


# ---------------------------------------------------------------------------
# 素材
# ---------------------------------------------------------------------------

_gui_cache = {}


def load_gui() -> Image.Image:
    if "img" in _gui_cache:
        return _gui_cache["img"]
    path = os.path.join(HERE, "assets", "gui_crop.png")
    img = Image.open(path).convert("RGB")
    img = img.resize((956, 751), Image.LANCZOS)
    canvas = Image.new("RGB", (956 + 2, 751 + 2), (58, 58, 66))
    canvas.paste(img, (1, 1))
    _gui_cache["img"] = canvas
    return canvas


def make_bg() -> Image.Image:
    """极轻的竖向渐变，避免纯黑死板。"""
    arr = np.zeros((H, W, 3), dtype=np.uint8)
    for y in range(H):
        k = y / H
        arr[y, :] = (int(15 + 6 * k), int(15 + 6 * k), int(18 + 8 * k))
    return Image.fromarray(arr)


_bg = make_bg()


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------

def scene_intro(t: float) -> Image.Image:
    img = _bg.copy()
    d = ImageDraw.Draw(img)
    a1 = fade(t, 0.20, 0.7, 3.7, 0.5)
    a2 = fade(t, 0.75, 0.8, 3.7, 0.5)

    if a1 > 0.01:
        draw_text(d, W / 2, 430, "B 站评论区现状", 34, mix(BG, DIM, a1), center=True)
    if a2 > 0.02:
        draw_text(d, W / 2, 486, "机器人比观众来得还快", 78,
                  mix(BG, INK, a2), bold=True, center=True)

    a3 = fade(t, 1.7, 1.0, 3.7, 0.5)
    if a3 > 0.01:
        draw_text(d, W / 2, 620, "一条视频发出去，十分钟内就能刷到四五个", 30,
                  mix(BG, DIM, a3), center=True)
    return img


COMMENTS = [
    ("旅行者的大肥鱼", "本鲸看完啦，这期视频的节奏把握得真到位，学到了很多", True),
    ("深海的大肥鱼", "总结一下：首先选题有意思，其次画面也用心，最后配乐选得不错", True),
    ("老王", "说得好，三连了", False),
]


def scene_comments(t: float) -> Image.Image:
    img = _bg.copy()
    d = ImageDraw.Draw(img)

    a = fade(t, 0.0, 0.5, 6.5, 0.5)
    if a > 0.01:
        draw_text(d, 260, 150, "这些评论，你的观众也在看", 52, mix(BG, INK, a), bold=True)
        draw_text(d, 260, 222, "机器人账号会接管真实用户，自动刷视频、自动发总结", 28,
                  mix(BG, DIM, a))

    for i, (name, text, bot) in enumerate(COMMENTS):
        start = 0.7 + i * 0.55
        k = ease_out(clamp((t - start) / 0.55))
        if k <= 0:
            continue
        y = 300 + i * 190 + (1 - k) * 26
        alpha = k

        x0, x1 = 260, 1660
        d.rounded_rectangle([x0, y, x1, y + 150], radius=14,
                            fill=mix(BG, CARD2 if i == 1 else CARD, alpha))
        if bot:
            d.rounded_rectangle([x0, y, x1, y + 150], radius=14,
                                outline=mix(BG, RED, alpha * 0.9), width=2)
        else:
            d.rounded_rectangle([x0, y, x1, y + 150], radius=14,
                                outline=mix(BG, (60, 90, 70), alpha), width=2)

        # 头像占位
        d.ellipse([x0 + 28, y + 34, x0 + 96, y + 102],
                  fill=mix(BG, (58, 58, 70) if bot else (52, 62, 56), alpha))
        draw_text(d, x0 + 62, y + 62, name[:1], 28, mix(BG, DIM, alpha), center=True)

        draw_text(d, x0 + 118, y + 30, name, 30, mix(BG, INK, alpha), bold=True)
        draw_text(d, x0 + 118, y + 78, text[:34], 27, mix(BG, (200, 198, 194), alpha))

        tag = "AI 机器人" if bot else "真人"
        tag_color = RED if bot else GREEN
        tw = measure(tag, 22) + 28
        d.rounded_rectangle([x1 - tw - 26, y + 26, x1 - 26, y + 62], radius=8,
                            fill=mix(BG, tag_color, alpha * 0.16))
        draw_text(d, x1 - tw - 12, y + 32, tag, 22, mix(BG, tag_color, alpha), bold=True)

    a2 = fade(t, 3.2, 0.7, 6.5, 0.5)
    if a2 > 0.01:
        draw_text(d, 260, 900, "昵称都带「大肥鱼」，正文是一段格式化总结", 30,
                  mix(BG, AMBER, a2))
    return img


def scene_userscript(t: float) -> Image.Image:
    img = _bg.copy()
    d = ImageDraw.Draw(img)

    a1 = fade(t, 0.0, 0.5, 4.4, 0.5)
    if a1 > 0.01:
        draw_text(d, 200, 330, "油猴脚本让你自己看不见", 66, mix(BG, INK, a1), bold=True)

    a2 = fade(t, 0.7, 0.6, 4.4, 0.5)
    if a2 > 0.01:
        draw_text(d, 200, 452, "评论还挂在那儿，别人进来照样能翻到", 40, mix(BG, DIM, a2))

    a3 = fade(t, 1.8, 0.6, 4.4, 0.5)
    if a3 > 0.01:
        d.line([200, 560, 200 + 900 * a3, 560], fill=mix(BG, PINK, a3 * 0.7), width=3)
        draw_text(d, 200, 596, "要干净，得在服务端删掉", 44, mix(BG, PINK, a3), bold=True)
    return img


def scene_gui(t: float) -> Image.Image:
    img = _bg.copy()
    d = ImageDraw.Draw(img)

    k = fade(t, 0.0, 0.8)
    zoom = 1.0 + 0.03 * ease_out(clamp((t - 0.6) / 9.0))
    gui = load_gui()                       # 958 x 753
    base_w = 900.0
    gw = int(base_w * zoom)
    gh = int(gui.height * (gw / gui.width))
    gui2 = gui.resize((gw, gh), Image.LANCZOS)
    gx = (W - gw) // 2
    gy = 158
    mask = Image.new("L", (gw, gh), int(255 * k))
    img.paste(gui2, (gx, gy), mask)

    a = fade(t, 0.0, 0.5, 9.2, 0.5)
    if a > 0.01:
        draw_text(d, 150, 44, "fxxk-off-dfy 图形界面", 34, mix(BG, INK, a), bold=True)
        draw_text(d, 150, 94, "粘贴 Cookie，刷新投稿列表，点一下体检", 22, mix(BG, DIM, a))

    steps = [("1", "粘贴 Cookie", 2.2), ("2", "刷新投稿列表", 3.6), ("3", "点体检，先只打印不删", 5.0)]
    gap = 470
    sx = (W - gap * 3) // 2 + 40
    for i, (num, label, st) in enumerate(steps):
        kk = fade(t, st, 0.45)
        if kk <= 0.01:
            continue
        x = sx + i * gap
        y = 918
        d.ellipse([x, y, x + 36, y + 36], fill=mix(BG, PINK, kk * 0.85))
        draw_text(d, x + 18, y + 7, num, 22, mix(BG, (255, 255, 255), kk), center=True)
        draw_text(d, x + 52, y + 3, label, 26, mix(BG, INK, kk))

    a3 = fade(t, 6.2, 0.8)
    if a3 > 0.01:
        draw_text(d, W / 2, 1000, "命中规则直接删，可选举报和拉黑", 24,
                  mix(BG, DIM, a3), center=True)
    return img


TERM_LINES = [
    ("$ python fxxk_off_dfy.py check --all", INK, 0.0),
    ("扫描 BV1yVhC63EKJ  哈基人的早餐被吃了（全量历史）", DIM, 0.55),
    ("评论总数 328（含楼中楼） / 页数 6 / 扫描 224 / 命中 4", DIM, 1.10),
    ("[DRY] 命中 name:/大肥鱼/ | score=100 | 旅行者的大肥鱼: 本鲸……", (168, 162, 158), 1.70),
    ("[DRY] 命中 name:/大肥鱼/ | score=100 | 深海的大肥鱼: 总结一下……", (168, 162, 158), 2.25),
    ("", INK, 2.80),
    ("$ python fxxk_off_dfy.py run --live", INK, 3.10),
    ("已删 name:/大肥鱼/ | score=100 | 旅行者的大肥鱼: 本鲸……", GREEN, 3.55),
    ("已删 name:/大肥鱼/ | score=100 | 深海的大肥鱼: 总结一下……", GREEN, 3.95),
    ("本轮汇总：删除 2 / 举报 2 / 失败 0", (220, 218, 214), 4.35),
]


def scene_terminal(t: float) -> Image.Image:
    img = _bg.copy()
    d = ImageDraw.Draw(img)

    k = fade(t, 0.0, 0.6, 10.9, 0.6)
    x0, y0, x1, y1 = 170, 130, 1750, 866
    d.rounded_rectangle([x0, y0, x1, y1], radius=12, fill=mix(BG, (20, 20, 24), k))
    d.rounded_rectangle([x0, y0, x1, y1], radius=12, outline=mix(BG, (48, 48, 56), k), width=2)

    for i, c in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        d.ellipse([x0 + 22 + i * 26, y0 + 18, x0 + 40 + i * 26, y0 + 36], fill=mix(BG, c, k * 0.9))
    draw_text(d, x0 + 26, y0 + 46, "Windows PowerShell", 20, mix(BG, DIM, k))

    y = y0 + 98
    for text, color, at in TERM_LINES:
        if t < at:
            break
        planned = int(len(text) * clamp((t - at) / 0.22))
        if planned > len(text) - 2:
            planned = len(text)
        visible = text[:planned]
        if visible:
            draw_text(d, x0 + 34, y, visible, 26, mix(BG, color, k))
        y += 52

    if int(t * 3) % 2 == 0:
        shown = sum(1 for _, _, at in TERM_LINES if t >= at)
        last_y = y0 + 98 + 52 * max(0, shown - 1)
        d.rectangle([x0 + 34, last_y + 8, x0 + 46, last_y + 40], fill=mix(BG, INK, 0.7 * k))
    return img


def scene_points(t: float) -> Image.Image:
    img = _bg.copy()
    d = ImageDraw.Draw(img)

    a = fade(t, 0.0, 0.5, 7.4, 0.5)
    if a > 0.01:
        draw_text(d, 200, 220, "它做的事其实就三件", 58, mix(BG, INK, a), bold=True)

    items = [
        ("服务端删除，所有访客都看不到", 1.0),
        ("命中后可以顺带举报、拉黑", 1.9),
        ("纯 Python，没有第三方依赖", 2.8),
    ]
    for i, (label, st) in enumerate(items):
        kk = fade(t, st, 0.5)
        if kk <= 0.01:
            continue
        y = 390 + i * 175
        d.rectangle([200, y + 18, 200 + 46 * kk, y + 24], fill=mix(BG, PINK, kk))
        draw_text(d, 276, y, label, 44, mix(BG, INK, kk))

    a2 = fade(t, 4.2, 0.6, 7.4, 0.5)
    if a2 > 0.01:
        draw_text(d, 200, 930, "Windows 双击 4-gui.bat，Linux 直接 python fxxk_off_dfy.py run", 26,
                  mix(BG, DIM, a2))
    return img


def scene_outro(t: float) -> Image.Image:
    img = _bg.copy()
    d = ImageDraw.Draw(img)

    a1 = fade(t, 0.1, 0.6)
    if a1 > 0.01:
        draw_text(d, W / 2, 360, "fxxk-off-dfy", 96, mix(BG, INK, a1), bold=True, center=True)

    a2 = fade(t, 0.5, 0.6)
    if a2 > 0.01:
        draw_text(d, W / 2, 500, "github.com/NothingCooker/fxxk-off-dfy", 32,
                  mix(BG, PINK, a2), center=True)

    a3 = fade(t, 1.0, 0.6)
    if a3 > 0.01:
        draw_text(d, W / 2, 580, "MIT License，免费随便改", 26, mix(BG, DIM, a3), center=True)

    a4 = fade(t, 1.8, 0.6)
    if a4 > 0.01:
        bw = 560
        d.rounded_rectangle([W / 2 - bw / 2, 690, W / 2 + bw / 2, 770], radius=10,
                            outline=mix(BG, AMBER, a4 * 0.8), width=3)
        draw_text(d, W / 2, 712, "先体检，再开删", 40, mix(BG, AMBER, a4), bold=True, center=True)

    a5 = fade(t, 2.6, 0.6)
    if a5 > 0.01:
        draw_text(d, W / 2, 840, "删除不可撤销，跑一天 dry-run 看日志再开 live", 26,
                  mix(BG, DIM, a5), center=True)
    return img


SCENES = [
    (0.0, 4.5, scene_intro),
    (4.5, 11.5, scene_comments),
    (11.5, 16.5, scene_userscript),
    (16.5, 26.5, scene_gui),
    (26.5, 38.0, scene_terminal),
    (38.0, 46.0, scene_points),
    (46.0, 53.0, scene_outro),
]


def render(t: float) -> Image.Image:
    img = SCENES[-1][2](t - SCENES[-1][0])
    for start, end, fn in SCENES:
        if start <= t < end:
            img = fn(t - start)
            break
    # 结尾整体压暗，避免最后一帧硬切
    if t > 52.2:
        k = 1 - clamp((t - 52.2) / 0.8)
        img = Image.fromarray((np.array(img).astype(np.float32) * k).astype(np.uint8))
    return img


def main() -> int:
    test = "--test" in sys.argv
    encoder = "libopenh264"
    for arg in sys.argv:
        if arg.startswith("--encoder="):
            encoder = arg.split("=", 1)[1]
    out_path = os.path.join(HERE, "fxxk-off-dfy-promo-test.mp4" if test else "fxxk-off-dfy-promo.mp4")
    duration = 2.0 if test else SCENES[-1][1]
    total = int(duration * FPS)

    if encoder == "h264_nvenc":
        venc = ["-c:v", encoder, "-preset", "p6", "-rc", "vbr", "-cq", "19",
                "-b:v", "0", "-maxrate", "40M", "-bufsize", "80M"]
    else:
        venc = ["-c:v", encoder, "-b:v", "12M"]
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H), "-r", str(FPS),
        "-i", "-",
    ] + venc + ["-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
    print("encoder:", encoder)
    # stderr 必须落文件：走 PIPE 不读会把 64KB 缓冲写满，ffmpeg 卡死、Python 写 stdin 也跟着卡死
    log_path = os.path.join(HERE, "ffmpeg.log")
    logf = open(log_path, "wb")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=logf)

    t0 = __import__("time").time()
    try:
        for i in range(total):
            t = i / FPS
            frame = render(t)
            if frame.size != (W, H):
                frame = frame.resize((W, H))
            proc.stdin.write(frame.tobytes())
            if i % 150 == 0:
                el = __import__("time").time() - t0
                print("  frame %d/%d (%.1fs, %.1fs elapsed)" % (i, total, t, el), flush=True)
        proc.stdin.close()
    except BrokenPipeError:
        print("ffmpeg 提前退出，看 promo/ffmpeg.log")
    code = proc.wait()
    logf.close()
    if code != 0:
        print("ffmpeg failed:", open(log_path, encoding="utf-8", errors="replace").read()[-2000:])
        return code
    size = os.path.getsize(out_path)
    print("done -> %s (%.1f MB, %d frames, %.1fs, encoder=%s)"
          % (out_path, size / 1048576, total, duration, encoder))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
