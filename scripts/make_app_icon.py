"""Build Windows app icon (.ico) from circular brand logo."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app" / "static" / "img" / "logo.png"
OUT = ROOT / "app" / "static" / "img" / "app-icon.ico"
BG = (245, 240, 230, 255)


def circular(size: int) -> Image.Image:
    src = Image.open(SRC).convert("RGBA")
    pad = max(1, size // 25)
    inner = size - pad * 2
    logo = src.resize((inner, inner), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), BG)
    canvas.paste(logo, (pad, pad), logo)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(canvas, (0, 0), mask)
    return out


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [circular(s) for s in sizes]
    images[0].save(OUT, format="ICO", sizes=[(s, s) for s in sizes], append_images=images[1:])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
