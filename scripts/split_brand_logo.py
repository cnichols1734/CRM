"""Split the Origen lockup into its two marks and size them for email.

The master art is white and teal on solid black, one square canvas holding the
circle mark above the "origen realty" wordmark. Email needs those two pieces
separately, on transparency, small enough to sit in a masthead.

Two jobs here. First, recover an alpha channel: the black is background, not
paint, so every pixel is read as coverage of either white or teal and the
brand color is written back at full strength. Second, find the empty band
between the circle and the words and cut there.

    python3 scripts/split_brand_logo.py

Masters land in origen_realty_logos/. Email-sized assets, both transparent and
flattened onto the slate masthead color, land in static/images/brand/.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageChops

REPO = Path(__file__).resolve().parent.parent

SOURCE = REPO / 'origen_realty_logos' / 'origen logo - White and Blue.png'
MASTER_DIR = REPO / 'origen_realty_logos'
EMAIL_DIR = REPO / 'static' / 'images' / 'brand'

WHITE = (255, 255, 255)
TEAL = (78, 200, 205)
SLATE = (108, 127, 147)

# A teal pixel sits well below its own green and blue on the red channel. White
# and the gray fringe do not, whatever their brightness.
TEAL_RED_GAP = 24

# Rendered sizes are 86px for the masthead mark and 64px in the footer, so 256
# covers both at 2x with room to spare. The wordmark is wide and short.
MARK_PX = 256
WORDMARK_PX = 440


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--master-dir', type=Path, default=MASTER_DIR)
    parser.add_argument('--email-dir', type=Path, default=EMAIL_DIR)
    args = parser.parse_args(argv)

    if not args.source.exists():
        print(f'No logo at {args.source}', file=sys.stderr)
        return 1

    lockup = trim(recolor(Image.open(args.source)))
    mark, wordmark = split_stack(lockup)

    args.master_dir.mkdir(parents=True, exist_ok=True)
    args.email_dir.mkdir(parents=True, exist_ok=True)

    written = []
    written.append(save(mark, args.master_dir / 'og-mark.png'))
    written.append(save(wordmark, args.master_dir / 'origen-realty-wordmark.png'))

    for image, stem, box in (
        (mark, 'origen-og-mark', MARK_PX),
        (wordmark, 'origen-wordmark', WORDMARK_PX),
    ):
        sized = fit(image, box)
        written.append(save(sized, args.email_dir / f'{stem}.png'))
        written.append(save(flatten(sized, SLATE), args.email_dir / f'{stem}-slate.png'))

    for path in written:
        image = Image.open(path)
        print(f'{path.relative_to(REPO)}  {image.width}x{image.height}')
    return 0


def recolor(source: Image.Image) -> Image.Image:
    """Read black as empty and snap everything else to white or teal.

    The artwork is two flat colors, so an antialiased edge is that color at
    partial strength. Taking the brightest channel as coverage and writing the
    brand color back at full saturation keeps the edges smooth without dragging
    the black backing into them as a gray halo.
    """
    red, green, blue = source.convert('RGB').split()
    coverage = ImageChops.lighter(ImageChops.lighter(red, green), blue)

    teal = ImageChops.subtract(coverage, red).point(
        lambda v: 255 if v > TEAL_RED_GAP else 0
    )
    # Teal peaks at 205 on its brightest channel, so its coverage reads dimmer
    # than white's for the same amount of paint. Scale it back up or the mark
    # comes out semi-transparent.
    alpha = Image.composite(
        coverage.point(lambda v: min(255, round(v * 255 / max(TEAL)))),
        coverage,
        teal,
    )
    color = Image.composite(
        Image.new('RGB', source.size, TEAL),
        Image.new('RGB', source.size, WHITE),
        teal,
    )
    out = color.convert('RGBA')
    out.putalpha(alpha)
    return out


def split_stack(lockup: Image.Image) -> tuple[Image.Image, Image.Image]:
    """Cut the lockup at the widest blank band between mark and wordmark."""
    rows = row_coverage(lockup)
    cut = widest_gap(rows)
    if cut is None:
        raise SystemExit('Found no blank band between the mark and the wordmark.')
    start, end = cut
    return trim(lockup.crop((0, 0, lockup.width, start))), trim(
        lockup.crop((0, end, lockup.width, lockup.height))
    )


def row_coverage(image: Image.Image) -> list[int]:
    """Mean alpha per row. Resizing to one column is the cheap way to get it."""
    column = image.getchannel('A').resize((1, image.height), Image.BOX)
    return list(column.tobytes())


def widest_gap(rows: list[int]) -> tuple[int, int] | None:
    best: tuple[int, int] | None = None
    run_start: int | None = None
    for index, value in enumerate(rows + [1]):
        if value == 0:
            if run_start is None:
                run_start = index
            continue
        if run_start is not None:
            run = (run_start, index)
            if best is None or (run[1] - run[0]) > (best[1] - best[0]):
                best = run
            run_start = None
    return best


def trim(image: Image.Image) -> Image.Image:
    box = image.getchannel('A').getbbox()
    return image.crop(box) if box else image


def fit(image: Image.Image, box: int) -> Image.Image:
    """Scale down so the long edge lands on ``box``, keeping even pixel sizes."""
    scale = box / max(image.size)
    width = max(1, round(image.width * scale))
    height = max(1, round(image.height * scale))
    return image.resize((width, height), Image.LANCZOS)


def flatten(image: Image.Image, background: tuple[int, int, int]) -> Image.Image:
    """A copy on a solid band, for clients that mishandle PNG alpha."""
    plate = Image.new('RGBA', image.size, background + (255,))
    plate.alpha_composite(image)
    return plate


def save(image: Image.Image, path: Path) -> Path:
    image.save(path, 'PNG', optimize=True)
    return path


if __name__ == '__main__':
    raise SystemExit(main())
