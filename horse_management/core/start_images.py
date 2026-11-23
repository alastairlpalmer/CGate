"""The picture an iPhone or an iPad shows while the installed app starts
(docs/MOBILE_APP_PLAN.md, item D3).

An iPhone shows a white screen at the start of an installed web app
unless the page names a picture of exactly its screen size. So there is
one picture for each size, all the same: the app's icon on the page
colour. Android makes its own start screen from the manifest.

This module has no Django in it: ``tools/make_start_images.py`` reads the
same list to draw the files.
"""

# The page colour (static/manifest.json, background_color).
BACKGROUND = '#F5F2EA'

# (width, height, pixel ratio) of a screen held upright, in CSS pixels.
SCREENS = (
    # iPhones
    (440, 956, 3),   # 16 Pro Max
    (402, 874, 3),   # 16 Pro
    (430, 932, 3),   # 16 Plus, 15 Plus, 15 Pro Max, 14 Pro Max
    (393, 852, 3),   # 16, 15, 15 Pro, 14 Pro
    (428, 926, 3),   # 14 Plus, 13 Pro Max, 12 Pro Max
    (390, 844, 3),   # 14, 13, 13 Pro, 12, 12 Pro
    (375, 812, 3),   # 13 mini, 12 mini, 11 Pro, XS, X
    (414, 896, 3),   # 11 Pro Max, XS Max
    (414, 896, 2),   # 11, XR
    (375, 667, 2),   # SE (2nd and 3rd), 8
    # iPads
    (1024, 1366, 2),  # Pro 12.9"
    (834, 1194, 2),   # Pro 11", Air 10.9" (4th)
    (820, 1180, 2),   # Air 10.9" (5th), iPad 10.9"
    (810, 1080, 2),   # iPad 10.2"
    (768, 1024, 2),   # iPad 9.7", mini
)


def file_name(width, height, ratio):
    """start/start-1170x2532.png: the size in real pixels."""
    return f'start/start-{width * ratio}x{height * ratio}.png'


def media(width, height, ratio):
    """The media query that picks this picture, for a screen held upright."""
    return (
        f'(device-width: {width}px) and (device-height: {height}px) '
        f'and (-webkit-device-pixel-ratio: {ratio}) and (orientation: portrait)'
    )


def links():
    """``[{'file', 'media'}]`` for the page head, one for each screen."""
    return [{'file': file_name(*screen), 'media': media(*screen)} for screen in SCREENS]
