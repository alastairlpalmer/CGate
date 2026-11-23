"""Draw the pictures an iPhone or an iPad shows while the installed app
starts (docs/MOBILE_APP_PLAN.md, item D3).

One picture for each screen size in core/start_images.py: the app's icon
in the middle of the page colour. Run it again when the icon or the page
colour changes, or when a new screen size is added:

    python tools/make_start_images.py        (from horse_management/)

It needs Pillow, which the app already uses. It writes into
static/start/ and deletes no file: remove a picture by hand when its
screen size leaves the list.
"""

import importlib.util
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ICON = ROOT / 'static' / 'icons' / 'icon-512.png'
# The icon's width as a part of the screen's width.
ICON_PART = 0.26


def load_screens():
    # By path, so this script needs no Django settings.
    spec = importlib.util.spec_from_file_location('start_images', ROOT / 'core' / 'start_images.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    screens = load_screens()
    icon = Image.open(ICON).convert('RGBA')
    background = tuple(int(screens.BACKGROUND[i:i + 2], 16) for i in (1, 3, 5))
    for width, height, ratio in screens.SCREENS:
        size = (width * ratio, height * ratio)
        picture = Image.new('RGB', size, background)
        side = round(min(size) * ICON_PART)
        mark = icon.resize((side, side), Image.LANCZOS)
        picture.paste(mark, ((size[0] - side) // 2, (size[1] - side) // 2), mark)
        target = ROOT / 'static' / screens.file_name(width, height, ratio)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Few colours: a palette makes each file a few kilobytes.
        picture.quantize(colors=64, method=Image.Quantize.MEDIANCUT).save(target, optimize=True)
        print(f'{target.relative_to(ROOT)}  {size[0]}x{size[1]}  {target.stat().st_size // 1024} KB')


if __name__ == '__main__':
    main()
