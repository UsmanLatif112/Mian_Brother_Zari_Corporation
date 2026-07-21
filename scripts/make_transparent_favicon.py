"""Strip cream/white background from brand seal and rebuild favicons."""

from collections import deque
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
IMG = STATIC / "img"
SRC = IMG / "logo.png"


def near_bg(r, g, b, a):
    if a < 20:
        return True
    # white / cream paper
    if r > 230 and g > 225 and b > 210:
        return True
    if min(r, g, b) > 200 and abs(r - g) < 25 and abs(g - b) < 30 and (r + g + b) / 3 > 220:
        return True
    if r > 210 and g > 200 and b > 180 and (r + g + b) / 3 > 205:
        return True
    return False


def main():
    img = Image.open(SRC).convert("RGBA")
    px = img.load()
    w, h = img.size
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    opx = out.load()

    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if near_bg(r, g, b, a):
                opx[x, y] = (0, 0, 0, 0)
            else:
                opx[x, y] = (r, g, b, a)

    visited = [[False] * w for _ in range(h)]
    q = deque()
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (0, h // 2), (w - 1, h // 2), (w // 2, h - 1)]
    for sx, sy in seeds:
        visited[sy][sx] = True
        q.append((sx, sy))

    while q:
        x, y = q.popleft()
        r, g, b, a = opx[x, y]
        if a > 0 and not near_bg(r, g, b, a):
            continue
        if a > 0:
            opx[x, y] = (0, 0, 0, 0)
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not visited[ny][nx]:
                visited[ny][nx] = True
                nr, ng, nb, na = opx[nx, ny]
                if na == 0 or near_bg(nr, ng, nb, na):
                    q.append((nx, ny))

    bbox = out.getbbox()
    if bbox:
        cropped = out.crop(bbox)
        ow, oh = cropped.size
        side = max(ow, oh)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(cropped, ((side - ow) // 2, (side - oh) // 2), cropped)
        out = canvas

    out.save(IMG / "logo-transparent.png", optimize=True)
    for size, name in ((16, "favicon-16.png"), (32, "favicon-32.png"), (180, "apple-touch-icon.png")):
        out.resize((size, size), Image.Resampling.LANCZOS).save(IMG / name, optimize=True)

    icons = [out.resize((s, s), Image.Resampling.LANCZOS) for s in (16, 32, 48)]
    icons[0].save(
        STATIC / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
        append_images=icons[1:],
    )
    print("ok", out.size)


if __name__ == "__main__":
    main()
