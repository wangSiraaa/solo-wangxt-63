"""仅用 Pillow 实现的感知哈希（dHash，64 位）。

把图片转成 9x8 灰度图，比较相邻像素的明暗得到 64 位指纹；
两张图的汉明距离越小越相似。哈希只用于生成“疑似重复候选”，
不作为合并事件的依据。
"""
import hashlib
import io

from PIL import Image, ImageOps

HASH_SIZE = 8  # 8x8 = 64 位


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def perceptual_hash(data: bytes) -> int:
    """返回 64 位整数感知哈希。"""
    with Image.open(io.BytesIO(data)) as img:
        gray = ImageOps.grayscale(img)
        small = gray.resize((HASH_SIZE + 1, HASH_SIZE), Image.LANCZOS)
        pixels = list(small.getdata())
    bits = 0
    width = HASH_SIZE + 1
    for row in range(HASH_SIZE):
        for col in range(HASH_SIZE):
            left = pixels[row * width + col]
            right = pixels[row * width + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming_distance(hash_a: int, hash_b: int) -> int:
    return (hash_a ^ hash_b).bit_count()


def to_hex(value: int) -> str:
    return f"{value:016x}"


def from_hex(text: str) -> int:
    return int(text, 16)
