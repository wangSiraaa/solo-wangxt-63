"""生成模拟现场照片：确定性伪随机场景，便于测试感知哈希。"""
import io
import random

from PIL import Image, ImageDraw, ImageFilter


def scene_bytes(seed: int, size=(360, 240)) -> bytes:
    """一张“路面垃圾”模拟图：灰底 + 若干深色垃圾袋斑块。"""
    rng = random.Random(seed)
    img = Image.new("RGB", size, (168, 165, 160))
    draw = ImageDraw.Draw(img)
    # 路面砖缝
    for x in range(0, size[0], 40):
        draw.line([(x, 0), (x, size[1])], fill=(150, 148, 144), width=1)
    # 垃圾袋
    for _ in range(rng.randint(4, 7)):
        cx, cy = rng.randint(30, size[0] - 30), rng.randint(30, size[1] - 30)
        r = rng.randint(12, 26)
        shade = rng.randint(20, 60)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(shade, shade, shade))
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def variant_bytes(base: bytes, angle=0.0, crop_ratio=0.03, quality=92) -> bytes:
    """同一问题换个角度/压缩的模拟：轻微旋转 + 裁剪 + 重编码。"""
    img = Image.open(io.BytesIO(base))
    img = img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=(168, 165, 160))
    w, h = img.size
    dx, dy = int(w * crop_ratio), int(h * crop_ratio)
    img = img.crop((dx, dy, w - dx, h - dy)).resize((w, h))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()
