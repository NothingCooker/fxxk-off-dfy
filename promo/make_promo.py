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
FFMPEG = r"C:\Program Files\BlueStacks_nxt_cn\ffmpeg.exe"
COPY_PATH = os.path.join(HERE, "copy.json")

_fcache = {}


def f_zh(size: int, bold: bool = False):
    """中文和英文统一用微软雅黑。

    之前英文走 Consolas，两个字体 ascent/descent 不一样，同一行里中英文
    底部对不齐、字重也差一截，看起来像字体回落。统一之后基线天然对齐。
    """
    key = ("zh", size, bold)
    if key not in _fcache:
        _fcache[key] = ImageFont.truetype(FONT_ZH_B if bold else FONT_ZH, size)
    return _fcache[key]


def measure(text: str, size: int, bold: bool = False) -> float:
    return f_zh(size, bold).getlength(text)


def draw_text(d: ImageDraw.ImageDraw, x: float, y: float, text: str, size: int,
              color, bold: bool = False, center: bool = False) -> float:
    if center:
        x -= measure(text, size, bold) / 2
    d.text((x, y), text, font=f_zh(size, bold), fill=color)
    return x + measure(text, size, bold)


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
# 文案（全部来自 copy.json，可用 copy-editor.html 改）
# ---------------------------------------------------------------------------

import json

DEFAULT_COPY = {
    "intro": {"eyebrow": "", "headline": "", "sub": ""},
    "comments": {"headline": "", "sub": "", "hint": "", "tag_bot": "AI 机器人",
                 "tag_human": "真人", "items": []},
    "userscript": {"line1": "", "line2": "", "punch": ""},
    "gui": {"title": "", "sub": "", "steps": [], "note": ""},
    "terminal": {"window_title": "Windows PowerShell", "lines": []},
    "points": {"headline": "", "items": [], "footer": ""},
    "outro": {"title": "", "url": "", "license": "", "badge": "", "note": ""},
}


def load_copy() -> dict:
    data = json.loads(json.dumps(DEFAULT_COPY))
    if os.path.exists(COPY_PATH):
        with open(COPY_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
        for section, values in (user or {}).items():
            if section in data and isinstance(values, dict):
                data[section].update(values)
    return data


COPY = load_copy()


# ---------------------------------------------------------------------------
# 场景
# ---------------------------------------------------------------------------

def scene_intro(t: float) -> Image.Image:
    img = _bg.copy()
    d = ImageDraw.Draw(img)
    c = COPY["intro"]
    a1 = fade(t, 0.20, 0.7, 3.7, 0.5)
    a2 = fade(t, 0.75, 0.8, 3.7, 0.5)

    if a1 > 0.01:
        draw_text(d, W / 2, 430, c["eyebrow"], 34, mix(BG, DIM, a1), center=True)
    if a2 > 0.02:
        # 标题短的时候放大一点，免得四个字孤零零挂在中间
        n = len(c["headline"])
        hsize = 108 if n <= 4 else (94 if n <= 6 else 78)
        draw_text(d, W / 2, 486 - (hsize - 78) // 3, c["headline"], hsize,
                  mix(BG, INK, a2), bold=True, center=True)

    a3 = fade(t, 1.7, 1.0, 3.7, 0.5)
    if a3 > 0.01:
        draw_text(d, W / 2, 620, c["sub"], 30, mix(BG, DIM, a3), center=True)
    return img


def scene_comments(t: float) -> Image.Image:
    img = _bg.copy()
    d = ImageDraw.Draw(img)
    c = COPY["comments"]

    a = fade(t, 0.0, 0.5, 6.5, 0.5)
    if a > 0.01:
        draw_text(d, 260, 150, c["headline"], 52, mix(BG, INK, a), bold=True)
        draw_text(d, 260, 222, c["sub"], 28, mix(BG, DIM, a))

    for i, item in enumerate(c["items"]):
        name = str(item.get("name", ""))
        text = str(item.get("text", ""))
        bot = bool(item.get("bot"))
        # 评论少的时候整体下移，别让下半屏空着
        start_y = 300 + max(0, 3 - len(c["items"])) * 80
        start = 0.7 + i * 0.55
        k = ease_out(clamp((t - start) / 0.55))
        if k <= 0:
            continue
        y = start_y + i * 190 + (1 - k) * 26
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

        tag = c["tag_bot"] if bot else c["tag_human"]
        tag_color = RED if bot else GREEN
        tw = measure(tag, 22) + 28
        d.rounded_rectangle([x1 - tw - 26, y + 26, x1 - 26, y + 62], radius=8,
                            fill=mix(BG, tag_color, alpha * 0.16))
        draw_text(d, x1 - tw - 12, y + 32, tag, 22, mix(BG, tag_color, alpha), bold=True)

    a2 = fade(t, 3.2, 0.7, 6.5, 0.5)
    if a2 > 0.01:
        draw_text(d, 260, 900, c["hint"], 30, mix(BG, AMBER, a2))
    return img


def scene_userscript(t: float) -> Image.Image:
    img = _bg.copy()
    d = ImageDraw.Draw(img)
    c = COPY["userscript"]

    a1 = fade(t, 0.0, 0.5, 4.4, 0.5)
    if a1 > 0.01:
        draw_text(d, 200, 330, c["line1"], 66, mix(BG, INK, a1), bold=True)

    a2 = fade(t, 0.7, 0.6, 4.4, 0.5)
    if a2 > 0.01:
        draw_text(d, 200, 452, c["line2"], 40, mix(BG, DIM, a2))

    a3 = fade(t, 1.8, 0.6, 4.4, 0.5)
    if a3 > 0.01:
        d.line([200, 560, 200 + 900 * a3, 560], fill=mix(BG, PINK, a3 * 0.7), width=3)
        draw_text(d, 200, 596, c["punch"], 44, mix(BG, PINK, a3), bold=True)
    return img


def scene_gui(t: float) -> Image.Image:
    img = _bg.copy()
    d = ImageDraw.Draw(img)
    c = COPY["gui"]

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
        draw_text(d, 150, 44, c["title"], 34, mix(BG, INK, a), bold=True)
        draw_text(d, 150, 94, c["sub"], 22, mix(BG, DIM, a))

    steps = [("1", c["steps"][0], 2.2), ("2", c["steps"][1], 3.6), ("3", c["steps"][2], 5.0)]
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
        draw_text(d, W / 2, 1000, c["note"], 24, mix(BG, DIM, a3), center=True)
    return img


TERM_STYLE = {
    "cmd": INK,
    "normal": DIM,
    "dry": (168, 162, 158),
    "ok": GREEN,
    "result": (220, 218, 214),
}


def build_term_lines():
    """按 copy.json 的行顺序排时间：命令前停一下，结果行连得紧一点。"""
    items = COPY["terminal"]["lines"]
    out, at = [], 0.0
    for i, item in enumerate(items):
        if i:
            prev_empty = not COPY["terminal"]["lines"][i - 1].get("text")
            if item.get("style") == "cmd":
                at += 0.70
            elif prev_empty:
                at += 0.25
            else:
                at += 0.50
        text = str(item.get("text", ""))
        color = TERM_STYLE.get(str(item.get("style", "normal")), DIM)
        out.append((text, color, at))
    return out


TERM_LINES = build_term_lines()


def scene_terminal(t: float) -> Image.Image:
    img = _bg.copy()
    d = ImageDraw.Draw(img)
    c = COPY["terminal"]

    k = fade(t, 0.0, 0.6, 10.9, 0.6)
    x0, y0, x1, y1 = 170, 130, 1750, 866
    d.rounded_rectangle([x0, y0, x1, y1], radius=12, fill=mix(BG, (20, 20, 24), k))
    d.rounded_rectangle([x0, y0, x1, y1], radius=12, outline=mix(BG, (48, 48, 56), k), width=2)

    for i, dot in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        d.ellipse([x0 + 22 + i * 26, y0 + 18, x0 + 40 + i * 26, y0 + 36], fill=mix(BG, dot, k * 0.9))
    draw_text(d, x0 + 26, y0 + 46, c["window_title"], 20, mix(BG, DIM, k))

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
    c = COPY["points"]

    a = fade(t, 0.0, 0.5, 7.4, 0.5)
    if a > 0.01:
        draw_text(d, 200, 220, c["headline"], 58, mix(BG, INK, a), bold=True)

    for i, label in enumerate(c["items"]):
        kk = fade(t, 1.0 + i * 0.9, 0.5)
        if kk <= 0.01:
            continue
        y = 390 + i * 175
        d.rectangle([200, y + 18, 200 + 46 * kk, y + 24], fill=mix(BG, PINK, kk))
        draw_text(d, 276, y, label, 44, mix(BG, INK, kk))

    a2 = fade(t, 4.2, 0.6, 7.4, 0.5)
    if a2 > 0.01:
        draw_text(d, 200, 930, c["footer"], 26, mix(BG, DIM, a2))
    return img


def scene_outro(t: float) -> Image.Image:
    img = _bg.copy()
    d = ImageDraw.Draw(img)
    c = COPY["outro"]

    a1 = fade(t, 0.1, 0.6)
    if a1 > 0.01:
        draw_text(d, W / 2, 360, c["title"], 96, mix(BG, INK, a1), bold=True, center=True)

    a2 = fade(t, 0.5, 0.6)
    if a2 > 0.01:
        draw_text(d, W / 2, 500, c["url"], 32, mix(BG, PINK, a2), center=True)

    a3 = fade(t, 1.0, 0.6)
    if a3 > 0.01:
        draw_text(d, W / 2, 580, c["license"], 26, mix(BG, DIM, a3), center=True)

    a4 = fade(t, 1.8, 0.6)
    if a4 > 0.01:
        bw = max(560, measure(c["badge"], 40, True) + 120)
        d.rounded_rectangle([W / 2 - bw / 2, 690, W / 2 + bw / 2, 770], radius=10,
                            outline=mix(BG, AMBER, a4 * 0.8), width=3)
        draw_text(d, W / 2, 712, c["badge"], 40, mix(BG, AMBER, a4), bold=True, center=True)

    a5 = fade(t, 2.6, 0.6)
    if a5 > 0.01:
        draw_text(d, W / 2, 840, c["note"], 26, mix(BG, DIM, a5), center=True)
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
