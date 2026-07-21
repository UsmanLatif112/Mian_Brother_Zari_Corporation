"""Build circular favicon with solid background (visible on dark tabs)."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
IMG = STATIC / "img"
SRC = IMG / "logo.png"
# Soft cream matching the seal interior
BG = (245, 240, 230, 255)


def circle_mask(size: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size - 1, size - 1), fill=255)
    return mask


def make_circular(src: Image.Image, size: int, bg=BG) -> Image.Image:
    # Fit logo into circle with small padding
    pad = int(size * 0.04)
    inner = size - pad * 2
    logo = src.convert("RGBA").resize((inner, inner), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (size, size), bg)
    canvas.paste(logo, (pad, pad), logo)

    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(canvas, (0, 0), circle_mask(size))
    return out


def main():
    src = Image.open(SRC)
    for size, name in ((16, "favicon-16.png"), (32, "favicon-32.png"), (180, "apple-touch-icon.png")):
        make_circular(src, size).save(IMG / name, optimize=True)

    icons = [make_circular(src, s) for s in (16, 32, 48)]
    icons[0].save(
        STATIC / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
        append_images=icons[1:],
    )
    # Preview for login/sidebar optional: keep logo.png as-is
    make_circular(src, 256).save(IMG / "favicon-circle.png", optimize=True)
    print("ok circular favicon with cream bg")


if __name__ == "__main__":
    main()
