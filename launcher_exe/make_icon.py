"""Ve icon app.ico (tai nghe tren nen tim). Chay 1 lan: python make_icon.py"""
from pathlib import Path
from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
bg = Image.new("RGBA", (S, S))
px = bg.load()
for y in range(S):
    for x in range(S):
        t = (x + y) / (2 * S)
        px[x, y] = (int(124 + (37 - 124) * t), int(58 + (99 - 58) * t), int(237 + (235 - 237) * t), 255)
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((8, 8, S - 8, S - 8), radius=56, fill=255)
img.paste(bg, (0, 0), mask)
d = ImageDraw.Draw(img)
W = (255, 255, 255, 255)
d.arc((52, 44, 204, 196), start=180, end=360, fill=W, width=18)      # vong tai nghe
d.line((61, 120, 61, 150), fill=W, width=18)
d.line((195, 120, 195, 150), fill=W, width=18)
d.rounded_rectangle((40, 132, 92, 206), radius=16, fill=W)          # 2 bao tai
d.rounded_rectangle((164, 132, 216, 206), radius=16, fill=W)
out = Path(__file__).with_name("app.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save(out.with_suffix(".png"))
print("ok")
