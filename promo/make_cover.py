#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 B 站投稿封面（1920x1080 + 1146x717 两版）。"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

from make_promo import (BG, DIM, INK, PINK, RED, W, H, draw_text, f_en, f_zh,
                        load_gui, make_bg)

HERE = os.path.dirname(os.path.abspath(__file__))


def cover_1920() -> Image.Image:
    img = make_bg()
    d = ImageDraw.Draw(img)

    # 左侧一条粉色竖线
    d.rectangle([120, 250, 128, 700], fill=PINK)

    draw_text(d, 176, 214, "fxxk-off-dfy", 34, DIM, bold=True)

    draw_text(d, 176, 300, "机器人评论", 96, INK, bold=True)
    draw_text(d, 176, 420, "直接删掉", 96, PINK, bold=True)
    draw_text(d, 180, 566, "B 站接口定时扫，命中就删，可选举报拉黑", 34, (190, 188, 184))
    draw_text(d, 180, 646, "纯 Python，无第三方依赖", 28, DIM)
    draw_text(d, 180, 880, "github.com/NothingCooker/fxxk-off-dfy", 26, (120, 120, 128))

    # 右侧截图，稍微倾斜
    gui = load_gui()
    gw = 780
    gh = int(gui.height * gw / gui.width)
    small = gui.resize((gw, gh), Image.LANCZOS).rotate(-4, expand=True,
                                                     resample=Image.BICUBIC,
                                                     fillcolor=(15, 15, 18))
    mask = Image.new("L", small.size, 235)
    img.paste(small, (1030, 250), mask)

    return img


def cover_1146(src: Image.Image) -> Image.Image:
    # B 站常用尺寸 1146x717，直接等比裁剪中间区域
    target_ratio = 1146 / 717
    w, h = src.size
    cur = w / h
    if cur > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        box = (left, 0, left + new_w, h)
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        box = (0, top, w, top + new_h)
    return src.crop(box).resize((1146, 717), Image.LANCZOS)


def main() -> int:
    big = cover_1920()
    big.save(os.path.join(HERE, "cover-1920.png"))
    small = cover_1146(big)
    small.save(os.path.join(HERE, "cover-1146.png"))
    print("saved cover-1920.png", big.size, "cover-1146.png", small.size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
