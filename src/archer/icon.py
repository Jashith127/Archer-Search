import sys

from PIL import Image, ImageDraw


def make_icon(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, size - 1, size - 1), fill=(37, 99, 235))
    s = size
    # Chevron "A" pointing up, like an arrowhead.
    draw.polygon(
        [
            (s * 0.50, s * 0.20),
            (s * 0.78, s * 0.78),
            (s * 0.62, s * 0.78),
            (s * 0.50, s * 0.52),
            (s * 0.38, s * 0.78),
            (s * 0.22, s * 0.78),
        ],
        fill="white",
    )
    return img


def write_ico(path: str) -> None:
    make_icon(256).save(path, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])


if __name__ == "__main__":
    write_ico(sys.argv[1])
