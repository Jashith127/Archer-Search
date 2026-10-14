import sys

from PIL import Image, ImageDraw

# The pixel mark from templates/pixel_mark.html: '#' full ink, '+' and ':' fainter, '-' empty.
ROWS = [
    "---#---",
    "--###--",
    "--###--",
    "-##-##-",
    "-##-##-",
    "##---##",
    "+:---:+",
]
INK = {"#": 1.0, "+": 0.62, ":": 0.34}


def make_icon(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = size
    draw.rounded_rectangle((0, 0, s - 1, s - 1), radius=s * 0.22, fill=(17, 17, 17), outline=(50, 50, 50))
    pitch = s * 0.11
    off = (s - pitch * len(ROWS)) / 2
    sq = pitch * 0.8
    for y, row in enumerate(ROWS):
        for x, ch in enumerate(row):
            if ch in INK:
                x0, y0 = off + x * pitch + (pitch - sq) / 2, off + y * pitch + (pitch - sq) / 2
                alpha = round(255 * INK[ch])
                draw.rounded_rectangle((x0, y0, x0 + sq, y0 + sq), radius=sq * 0.2, fill=(237, 237, 237, alpha))
    return img


def write_ico(path: str) -> None:
    make_icon(256).save(path, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])


if __name__ == "__main__":
    write_ico(sys.argv[1])
