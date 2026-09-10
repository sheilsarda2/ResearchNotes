"""Crop and rearrange the four input streams from the authors' overview."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
CROPS = [
    ("Robot mesh RGB", "M rgb", (693, 19, 810, 301)),
    ("End-effector depth", "D eef", (817, 19, 934, 301)),
    ("Static scene RGB", "B rgb", (693, 337, 810, 619)),
    ("Scene depth", "D scene", (817, 337, 934, 619)),
]


def font(size):
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    raise FileNotFoundError("Install Arial or DejaVu Sans to rebuild this figure")


def main():
    source = Image.open(ROOT / "method-overview.png").convert("RGB")
    if source.size != (1656, 705):
        raise ValueError("Crop coordinates require the original 1656 x 705 overview")
    canvas = Image.new("RGB", (1000, 610), "#faf9f6")
    draw = ImageDraw.Draw(canvas)
    draw.text((28, 22), "RoFacto’s four conditioning streams", font=font(28), fill="#193d34")
    draw.text((28, 65), "Detail of the authors’ method figure. Time runs downward in each column.", font=font(20), fill="#536460")
    for index, (title, symbol, box) in enumerate(CROPS):
        center = 139 + index * 242
        draw.text((center, 106), title, anchor="mt", font=font(21), fill="#193d34")
        draw.text((center, 138), symbol, anchor="mt", font=font(18), fill="#536460")
        column = source.crop(box).resize((175, 423), Image.Resampling.LANCZOS)
        canvas.paste(column, (52 + index * 242, 170))
    canvas.save(ROOT / "conditioning-streams.png")


if __name__ == "__main__":
    main()
