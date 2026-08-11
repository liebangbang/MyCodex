#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 MyCode 科技感 macOS 应用图标 (.icns)——更亮、霓虹紫/青渐变 + 光晕 + 点阵。"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

SIZE = 1024
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
OUT_DIR = "/tmp/mycode_iconbuild"
ICONSET = os.path.join(OUT_DIR, "AppIcon.iconset")
os.makedirs(ICONSET, exist_ok=True)


def vgradient(size, stops):
    """多段垂直渐变。"""
    px = []
    for y in range(size):
        t = y / (size - 1)
        col = stops[-1][1]
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                local = (t - p0) / (p1 - p0) if p1 > p0 else 0
                col = tuple(int(c0[j] + (c1[j] - c0[j]) * local) for j in range(3))
                break
        px.extend([col] * size)
    img = Image.new("RGB", (size, size))
    img.putdata(px)
    return img


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def alpha_clip(src, mask):
    """把 src 的 alpha 通道限制在 mask 形状内。"""
    r, g, b, a = src.split()
    a = ImageChops_min(a, mask)
    return Image.merge("RGBA", (r, g, b, a))


def ImageChops_min(a, b):
    pa = a.load()
    pb = b.load()
    out = Image.new("L", a.size)
    po = out.load()
    for y in range(a.size[1]):
        for x in range(a.size[0]):
            po[x, y] = min(pa[x, y], pb[x, y])
    return out


def radial_glow(size, center, radius, color, alpha):
    """中心径向光晕。"""
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    steps = int(radius / 2)
    for i in range(steps, 0, -1):
        t = i / steps
        a = int(alpha * (1 - t * t))
        if a <= 2:
            continue
        r = radius * t
        c = color + (a,)
        d.ellipse(
            [center[0] - r, center[1] - r, center[0] + r, center[1] + r],
            outline=c,
            width=2,
        )
    return glow.filter(ImageFilter.GaussianBlur(radius=max(10, radius / 8)))


def grid_overlay(size, color, alpha, step=64, dot=4):
    """底部点阵网格。"""
    grid = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(grid)
    half = size // 2
    for x in range(step, size, step):
        for y in range(step, size, step):
            dx = abs(x - half) / half
            dy = abs(y - half) / half
            fade = max(0, 1 - (dx + dy) / 1.4)
            if fade <= 0.05:
                continue
            c = color + (int(alpha * fade),)
            d.ellipse([x - dot, y - dot, x + dot, y + dot], fill=c)
    return grid


def top_sheen(size, mask):
    """顶部强柔光。"""
    sheen = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(sheen)
    h = size // 3
    for y in range(h):
        t = y / h
        a = int(130 * (1 - t ** 0.65))
        if a > 0:
            d.line([(0, y), (size - 1, y)], fill=(255, 255, 255, a))
    sheen = sheen.filter(ImageFilter.GaussianBlur(radius=4))
    return alpha_clip(sheen, mask)


def bottom_glow(size, color, alpha):
    """底部水平反光。"""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    start = size * 2 // 3
    for y in range(start, size):
        t = (y - start) / (size - start)
        a_val = int(alpha * (1 - t))
        if a_val > 0:
            d.line([(0, y), (size - 1, y)], fill=color + (a_val,))
    return layer.filter(ImageFilter.GaussianBlur(radius=10))


def load_font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()


def main():
    radius = int(SIZE * 0.22)
    mask = rounded_mask(SIZE, radius)

    # 1) 更亮的科技感渐变：靛蓝紫 -> 电光紫 -> 亮粉 -> 科技青
    grad = vgradient(SIZE, [
        (0.0, (58, 48, 210)),    # 靛蓝紫
        (0.32, (124, 58, 237)),  # 电光紫
        (0.62, (192, 38, 211)),  # 亮粉
        (1.0, (6, 182, 212)),    # 科技青
    ])

    bg = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    bg.paste(grad, (0, 0), mask)

    # 2) 中央紫色光晕
    glow = radial_glow(SIZE, (SIZE // 2, SIZE // 2 - 30), SIZE * 0.55, (168, 85, 247), 150)
    bg = Image.alpha_composite(bg, alpha_clip(glow, mask))

    # 3) 底部点阵网格
    grid = grid_overlay(SIZE, (255, 255, 255), 45, step=56, dot=3)
    bg = Image.alpha_composite(bg, alpha_clip(grid, mask))

    # 4) 底部青色反光
    bg = Image.alpha_composite(bg, alpha_clip(bottom_glow(SIZE, (34, 211, 238), 70), mask))

    # 5) 顶部强高光
    bg = Image.alpha_composite(bg, top_sheen(SIZE, mask))

    # 6) 内发光描边
    inner = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(inner)
    d.rounded_rectangle(
        [6, 6, SIZE - 7, SIZE - 7],
        radius=radius - 2,
        outline=(255, 255, 255, 45),
        width=3,
    )
    bg = Image.alpha_composite(bg, inner)

    # 7) </> 字形：紫色外发光 + 白色主体
    font = load_font(int(SIZE * 0.40))
    glyph = "</>"

    glow_text = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(glow_text)
    d.text((SIZE // 2, SIZE // 2), glyph, font=font, fill=(192, 132, 252, 220), anchor="mm")
    glow_text = glow_text.filter(ImageFilter.GaussianBlur(radius=20))
    bg = Image.alpha_composite(bg, glow_text)

    d = ImageDraw.Draw(bg)
    d.text((SIZE // 2, SIZE // 2), glyph, font=font, fill=(255, 255, 255, 255), anchor="mm")

    # 8) 导出各尺寸到 iconset
    specs = [
        ("icon_16x16.png", 16),
        ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32),
        ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128),
        ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256),
        ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512),
        ("icon_512x512@2x.png", 1024),
    ]
    for name, s in specs:
        img = bg.resize((s, s), Image.LANCZOS)
        img.save(os.path.join(ICONSET, name))

    print("iconset generated:", ICONSET)


if __name__ == "__main__":
    main()
