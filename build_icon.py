#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 MyCodex macOS 应用图标 (.icns) v2。
以用户提供的桌面图标（紫渐变 + 白色 </>）为底：
- 背景：垂直紫渐变（上 #4E5AC4 -> 下 #BA25AE，由原图线性拟合还原）
- 符号：白色 </> 倾斜 45° 并放大（1.25x），整体居中比例协调
- 圆角：225px（约 0.22 * 1024），与原图一致
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

SIZE = 1024
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
OUT_DIR = "/tmp/mycodex_iconbuild"
ICONSET = os.path.join(OUT_DIR, "AppIcon.iconset")
os.makedirs(ICONSET, exist_ok=True)

# 垂直渐变线性拟合参数：c = b*y + d（x 方向无变化）
GRAD = {
    "R": (0.1156, 76.1),
    "G": (-0.0573, 92.0),
    "B": (-0.0234, 196.9),
}
RADIUS = 225          # 圆角半径
ROTATE_DEG = 45       # 代码符号倾斜角度
SCALE = 1.45          # 相对原图符号的放大倍数（1.25 -> 1.45，更大）
ORIG_GLYPH_H = 300    # 原图符号像素高度


def load_font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1],
                                        radius=radius, fill=255)
    return m


def _paste_at(layer, x, y):
    """把 RGBA layer 放到 (x,y)，返回与画布同尺寸的合成层。"""
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.paste(layer, (x, y), layer)
    return out


def main():
    # 1) 背景：垂直紫渐变（带圆角遮罩）
    bg = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    bp = bg.load()
    for y in range(SIZE):
        r = max(0, min(255, int(GRAD["R"][0] * y + GRAD["R"][1])))
        g = max(0, min(255, int(GRAD["G"][0] * y + GRAD["G"][1])))
        b = max(0, min(255, int(GRAD["B"][0] * y + GRAD["B"][1])))
        for x in range(SIZE):
            bp[x, y] = (r, g, b, 255)
    mask = rounded_mask(SIZE, RADIUS)
    r, g, b, a = bg.split()
    bg = Image.merge("RGBA", (r, g, b, ImageChops_min(a, mask)))

    # 2) 渲染 </>：目标高度 = 原图符号高 * 放大倍数
    font = load_font(470)
    glyph = "</>"
    tmp = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((SIZE // 2, SIZE // 2), glyph, font=font,
                             fill=(255, 255, 255, 255), anchor="mm")
    bb = tmp.getbbox()
    w0, h0 = bb[2] - bb[0], bb[3] - bb[1]
    target_h = int(ORIG_GLYPH_H * SCALE)
    scale = target_h / h0
    sym = tmp.crop(bb).resize((max(1, int(w0 * scale)), target_h), Image.LANCZOS)

    # 3) 倾斜 45°（expand 保留完整，透明边缘）
    sym = sym.rotate(ROTATE_DEG, expand=True, resample=Image.BICUBIC)
    sx = (SIZE - sym.width) // 2
    sy = (SIZE - sym.height) // 2

    # 3.1) 投影阴影：暗色模糊副本，向下偏移，给符号立体感
    w0, h0 = sym.size
    sh = sym.split()[3].point(lambda a: int(a * 0.60))
    shadow = Image.merge("RGBA", (Image.new("L", (w0, h0), 20),
                                  Image.new("L", (w0, h0), 10),
                                  Image.new("L", (w0, h0), 40),
                                  sh)).filter(ImageFilter.GaussianBlur(radius=20))
    bg = Image.alpha_composite(bg, _paste_at(shadow, sx + int(SIZE * 0.014), sy + int(SIZE * 0.026)))

    # 3.2) 柔和外发光：浅紫光晕包裹符号，融入背景（不再生硬贴上去）
    ga = sym.split()[3].point(lambda a: int(a * 0.45))
    glow = Image.merge("RGBA", (Image.new("L", (w0, h0), 214),
                                Image.new("L", (w0, h0), 186),
                                Image.new("L", (w0, h0), 255),
                                ga)).filter(ImageFilter.GaussianBlur(radius=26))
    bg = Image.alpha_composite(bg, _paste_at(glow, sx, sy))

    # 3.3) 白色主体
    bg = Image.alpha_composite(bg, _paste_at(sym, sx, sy))

    # 4) 导出各尺寸 iconset
    specs = [
        ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
    ]
    for name, s in specs:
        bg.resize((s, s), Image.LANCZOS).save(os.path.join(ICONSET, name))
    print("iconset generated:", ICONSET)


def ImageChops_min(a, b):
    pa = a.load()
    pb = b.load()
    out = Image.new("L", a.size)
    po = out.load()
    for y in range(a.size[1]):
        for x in range(a.size[0]):
            po[x, y] = min(pa[x, y], pb[x, y])
    return out


if __name__ == "__main__":
    main()
