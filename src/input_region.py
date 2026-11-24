"""Read-only visual fallback. A rectangle is never an editable AX target."""
import numpy as np
import Quartz


def _longest_run(edge_row: np.ndarray) -> tuple[int, int]:
    """Longest horizontal edge run (gap <= 4px) in a row slice starting at x=5."""
    xs = np.flatnonzero(edge_row)
    if xs.size < 2:
        return (0, 0)
    breaks = np.flatnonzero(np.diff(xs) > 4) + 1
    starts = np.empty(breaks.size + 1, dtype=xs.dtype)
    ends = np.empty(breaks.size + 1, dtype=xs.dtype)
    starts[0] = xs[0]
    starts[1:] = xs[breaks]
    ends[:-1] = xs[breaks - 1]
    ends[-1] = xs[-1]
    spans = ends - starts
    best = int(np.argmax(spans))
    if spans[best] <= 0:
        return (0, 0)
    return int(starts[best]) + 5, int(ends[best]) + 5


def input_outline(image):
    """Find the long separator above the chat app's message composer.

    Older builds draw a closed rectangle around the composer. Current 4.x draws
    only its top separator and left divider because the composer ends at the window
    bottom. Accept either shape, but only let an open-bottom separator qualify when it
    reaches the right window edge. Coordinates are normalized top-origin; no fixed
    sidebar width or input height is assumed. Ambiguous frames return None.
    """
    width, height = Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)
    if width <= 0 or height <= 0:
        return None
    w, h = 640, round(height * 640 / width)
    if h < 80:
        return None
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    ctx = Quartz.CGBitmapContextCreate(arr, w, h, 8, w * 4,
        Quartz.CGColorSpaceCreateDeviceRGB(), Quartz.kCGImageAlphaPremultipliedLast)
    if ctx is None:
        return None
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, w, h), image)
    y0 = int(h * 0.50)
    # |mean(rgb[y]) - mean(rgb[y+1])| >= 2  <=>  |sum(rgb[y]) - sum(rgb[y+1])| >= 6
    rgb_sum = arr[y0:h - 1, 5:w - 5, :3].sum(axis=2, dtype=np.int16)
    edges = np.abs(rgb_sum[:-1] - rgb_sum[1:]) >= 6
    rows = [(y0 + i, *_longest_run(edges[i])) for i in range(edges.shape[0])]
    tops = [(right-left, y, left, right) for y,left,right in rows
            if y < h*.90 and right-left > w*.40]
    if not tops:
        return None
    _, y, left, right = max(tops)
    bottoms = [(by, bl, br) for by,bl,br in rows
               if by > max(y+20,h*.90) and abs(bl-left)<12 and abs(br-right)<12]
    if not bottoms:
        # Current 4.x has no lower stroke: the input panel simply continues to the
        # bottom of the window. Requiring the detected separator to touch the right
        # edge keeps message bubbles and other internal rules from becoming targets.
        if right < w-12 or h-y < max(20,h*.08):
            return None
        by, bl, br = h-1, left, w-1
    else:
        by, bl, br = max(bottoms)
    return (min(left,bl)/w, y/h, (max(right,br)-min(left,bl))/w, (by-y)/h)


def locate_visual_input(win):
    from perception import capture_image
    try:
        image = capture_image(win['wid'])
        if image is None:
            return None
        rect = input_outline(image)
    except Exception:
        return None
    if rect is None:
        return None
    x, y, w, h = rect
    return (win['x']+x*win['w'], win['y']+y*win['h'], w*win['w'], h*win['h'])
