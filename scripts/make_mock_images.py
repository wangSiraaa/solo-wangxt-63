#!/usr/bin/env python
"""生成模拟现场照片到 mock_images/（确定性，可重复生成）。

- garbage_a.png       网格A的垃圾场景原图
- garbage_a_angle2.jpg 同一问题换角度/重压缩（感知哈希相近）
- garbage_a_copy.png  与 garbage_a.png 字节完全一致（跨地点误传用）
- garbage_b.png       另一个场景（复发/不同问题用）
"""
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from assessment.tests.helpers import scene_bytes, variant_bytes

OUT = pathlib.Path(__file__).resolve().parent.parent / "mock_images"


def main():
    OUT.mkdir(exist_ok=True)
    a = scene_bytes(1)
    (OUT / "garbage_a.png").write_bytes(a)
    (OUT / "garbage_a_angle2.jpg").write_bytes(variant_bytes(a))
    shutil.copy(OUT / "garbage_a.png", OUT / "garbage_a_copy.png")
    (OUT / "garbage_b.png").write_bytes(scene_bytes(2))
    for f in sorted(OUT.iterdir()):
        print(f"{f.name}\t{f.stat().st_size} bytes")


if __name__ == "__main__":
    main()
