import numpy as np
from PIL import Image

try:
    from numba import njit
except ImportError:
    def njit(f=None, **kwargs):
        if f is None:
            return lambda fn: fn
        return f

# Precompute Bayer 32x32 matrix once at module load
def _gen_bayer_recursive(n):
    if n == 2:
        return [[0, 2], [3, 1]]
    smaller = _gen_bayer_recursive(n // 2)
    result = [[0] * n for _ in range(n)]
    for y in range(n // 2):
        for x in range(n // 2):
            result[y][x] = 4 * smaller[y][x]
            result[y][x + n // 2] = 4 * smaller[y][x] + 2
            result[y + n // 2][x] = 4 * smaller[y][x] + 3
            result[y + n // 2][x + n // 2] = 4 * smaller[y][x] + 1
    return result

BAYER_32_MATRIX = _gen_bayer_recursive(32)

# Precompute Blue Noise 64x64 matrix once at module load
import random
_rnd = random.Random(42)
_flat_blue_noise = list(range(4096))
_rnd.shuffle(_flat_blue_noise)
BLUE_NOISE_MATRIX = [_flat_blue_noise[i*64:(i+1)*64] for i in range(64)]

# Thread-safe global cache for threshold matrices
_THRESH_CACHE = {}
_DOTDIFF_CACHE = {}
_RIEMERSMA_CACHE = {}

def get_threshold_matrix(mode):
    if mode in _THRESH_CACHE:
        return _THRESH_CACHE[mode]
    
    if mode == "Bayer 2x2":
        matrix = [[0, 2], [3, 1]]
        div, size = 4, 2
    elif mode == "Bayer 3x3":
        matrix = [[0, 7, 3], [6, 5, 2], [4, 1, 8]]
        div, size = 9, 3
    elif mode == "Bayer 4x4":
        matrix = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]
        div, size = 16, 4
    elif mode == "Halftone":
        matrix = [[12, 8, 4, 11], [9, 0, 1, 5], [13, 3, 2, 6], [15, 10, 7, 14]]
        div, size = 16, 4
    elif mode == "Bayer 16x16":
        matrix = [
            [  0, 128,  32, 160,   8, 136,  40, 168,   2, 130,  34, 162,  10, 138,  42, 170],
            [192,  64, 224,  96, 200,  72, 232, 104, 194,  66, 226,  98, 202,  74, 234, 106],
            [ 48, 176,  16, 144,  56, 184,  24, 152,  50, 178,  18, 146,  58, 186,  26, 154],
            [240, 112, 208,  80, 248, 120, 216,  88, 242, 114, 210,  82, 250, 122, 218,  90],
            [ 12, 140,  44, 172,   4, 132,  36, 164,  14, 142,  46, 174,   6, 134,  38, 166],
            [204,  76, 236, 108, 196,  68, 228, 100, 206,  78, 238, 110, 198,  70, 230, 102],
            [ 60, 188,  28, 156,  52, 180,  20, 148,  62, 190,  30, 158,  54, 182,  22, 150],
            [252, 124, 220,  92, 244, 116, 212,  84, 254, 126, 222,  94, 246, 118, 214,  86],
            [  3, 131,  35, 163,  11, 139,  43, 171,   1, 129,  33, 161,   9, 137,  41, 169],
            [195,  67, 227,  99, 203,  75, 235, 107, 193,  65, 225,  97, 201,  73, 233, 105],
            [ 51, 179,  19, 147,  59, 187,  27, 155,  49, 177,  17, 145,  57, 185,  25, 153],
            [243, 115, 211,  83, 251, 123, 219,  91, 241, 113, 209,  81, 249, 121, 217,  89],
            [ 15, 143,  47, 175,   7, 135,  39, 167,  13, 141,  45, 173,   5, 133,  37, 165],
            [207,  79, 239, 111, 199,  71, 231, 103, 205,  77, 237, 109, 197,  69, 229, 101],
            [ 63, 191,  31, 159,  55, 183,  23, 151,  61, 189,  29, 157,  53, 181,  21, 149],
            [255, 127, 223,  95, 247, 119, 215,  87, 253, 125, 221,  93, 245, 117, 213,  85]
        ]
        div, size = 256, 16
    elif mode == "Bayer 32x32":
        matrix = BAYER_32_MATRIX
        div, size = 1024, 32
    elif mode == "Blue Noise 64x64":
        matrix = BLUE_NOISE_MATRIX
        div, size = 4096, 64
    else:  # Bayer 8x8 default
        matrix = [
            [ 0, 48, 12, 60,  3, 51, 15, 63],
            [32, 16, 44, 28, 35, 19, 47, 31],
            [ 8, 56,  4, 52, 11, 59,  7, 55],
            [40, 24, 36, 20, 43, 27, 39, 23],
            [ 2, 50, 14, 62,  1, 49, 13, 61],
            [34, 18, 46, 30, 33, 17, 45, 29],
            [10, 58,  6, 54,  9, 57,  5, 53],
            [42, 26, 38, 22, 41, 25, 37, 21]
        ]
        div, size = 64, 8

    thresh = np.array([[int((matrix[y][x] / div) * 255) for x in range(size)] for y in range(size)], dtype=np.uint8)
    _THRESH_CACHE[mode] = (thresh, size)
    return thresh, size

def apply_ordered_dither(gray_img, mode, u=-0.25, v=-0.60):
    """Performs structured Bayer ordered dithering or halftone simulation directly on gray frames."""
    gray_copy = gray_img.copy()
    
    if mode == "Flipnote Memory Saver (Experimental)":
        img_256 = gray_copy.resize((256, 192), Image.Resampling.LANCZOS)
        pixels = img_256.load()
        
        brightness_offset = -int(u * 55)
        dither_width = int(90 * (v + 1.0))
        dither_width = max(0, min(120, dither_width))
        
        center_threshold = 128
        lower_bound = center_threshold - dither_width
        upper_bound = center_threshold + dither_width
        step = dither_width / 3.0 if dither_width > 0 else 1.0
        
        for y in range(192):
            if y % 2 == 1:
                for x in range(256):
                    pixels[x, y] = 255
            else:
                for x in range(256):
                    val = pixels[x, y] + brightness_offset
                    val = max(0, min(255, val))
                    
                    if val < lower_bound:
                        pixels[x, y] = 0
                    elif val > upper_bound:
                        pixels[x, y] = 255
                    else:
                        if val < lower_bound + step:
                            pixels[x, y] = 0 if x % 4 != 0 else 255
                        elif val < lower_bound + 2 * step:
                            pixels[x, y] = 0 if x % 2 == 0 else 255
                        else:
                            pixels[x, y] = 0 if x % 4 == 0 else 255
        
        target_w, target_h = gray_copy.size
        img_upscaled = img_256.resize((target_w, target_h), Image.Resampling.BILINEAR)
        return img_upscaled

    # Tiled thresholding in NumPy (highly parallelized vectorized operation)
    thresh_matrix, size = get_threshold_matrix(mode)
    arr = np.array(gray_copy, dtype=np.uint8)
    h, w = arr.shape
    
    reps_y = (h + size - 1) // size
    reps_x = (w + size - 1) // size
    tiled_thresh = np.tile(thresh_matrix, (reps_y, reps_x))[:h, :w]
    
    bw_arr = np.where(arr > tiled_thresh, 255, 0).astype(np.uint8)
    return Image.fromarray(bw_arr, mode='L')


@njit(cache=True)
def _jjn_kernel(buf, height, width):
    for y in range(height):
        for x in range(width):
            old_val = buf[y, x]
            new_val = 255.0 if old_val > 127.0 else 0.0
            buf[y, x] = new_val
            err = (old_val - new_val) / 48.0
            if err == 0.0:
                continue
            err7 = err * 7.0
            err5 = err * 5.0
            err3 = err * 3.0
            if x + 1 < width:
                buf[y, x + 1] += err7
            if x + 2 < width:
                buf[y, x + 2] += err5
            if y + 1 < height:
                if x - 2 >= 0:
                    buf[y + 1, x - 2] += err3
                if x - 1 >= 0:
                    buf[y + 1, x - 1] += err5
                buf[y + 1, x] += err7
                if x + 1 < width:
                    buf[y + 1, x + 1] += err5
                if x + 2 < width:
                    buf[y + 1, x + 2] += err3
            if y + 2 < height:
                if x - 2 >= 0:
                    buf[y + 2, x - 2] += err
                if x - 1 >= 0:
                    buf[y + 2, x - 1] += err3
                buf[y + 2, x] += err5
                if x + 1 < width:
                    buf[y + 2, x + 1] += err3
                if x + 2 < width:
                    buf[y + 2, x + 2] += err


@njit(cache=True)
def _stevenson_arce_kernel(buf, height, width):
    for y in range(height):
        for x in range(width):
            old_val = buf[y, x]
            new_val = 255.0 if old_val > 127.0 else 0.0
            buf[y, x] = new_val
            err = (old_val - new_val) / 200.0
            if err == 0.0:
                continue
            if x + 2 < width:
                buf[y, x + 2] += err * 32.0
            if y + 1 < height:
                if x - 3 >= 0:
                    buf[y + 1, x - 3] += err * 12.0
                if x - 1 >= 0:
                    buf[y + 1, x - 1] += err * 26.0
                if x + 1 < width:
                    buf[y + 1, x + 1] += err * 30.0
                if x + 3 < width:
                    buf[y + 1, x + 3] += err * 16.0
            if y + 2 < height:
                if x - 2 >= 0:
                    buf[y + 2, x - 2] += err * 12.0
                buf[y + 2, x] += err * 26.0
                if x + 2 < width:
                    buf[y + 2, x + 2] += err * 12.0
            if y + 3 < height:
                if x - 3 >= 0:
                    buf[y + 3, x - 3] += err * 5.0
                if x - 1 >= 0:
                    buf[y + 3, x - 1] += err * 12.0
                if x + 1 < width:
                    buf[y + 3, x + 1] += err * 12.0
                if x + 3 < width:
                    buf[y + 3, x + 3] += err * 5.0


@njit(cache=True)
def _atkinson_kernel(buf, height, width):
    for y in range(height):
        for x in range(width):
            old_val = buf[y, x]
            new_val = 255.0 if old_val > 127.0 else 0.0
            buf[y, x] = new_val
            err = (old_val - new_val) / 8.0
            if err == 0.0:
                continue
            if x + 1 < width:
                buf[y, x + 1] += err
            if x + 2 < width:
                buf[y, x + 2] += err
            if y + 1 < height:
                if x - 1 >= 0:
                    buf[y + 1, x - 1] += err
                buf[y + 1, x] += err
                if x + 1 < width:
                    buf[y + 1, x + 1] += err
            if y + 2 < height:
                buf[y + 2, x] += err


@njit(cache=True)
def _sierra_3row_kernel(buf, height, width):
    for y in range(height):
        for x in range(width):
            old_val = buf[y, x]
            new_val = 255.0 if old_val > 127.0 else 0.0
            buf[y, x] = new_val
            err = (old_val - new_val) / 32.0
            if err == 0.0:
                continue
            err5 = err * 5.0
            err4 = err * 4.0
            err3 = err * 3.0
            err2 = err * 2.0
            if x + 1 < width:
                buf[y, x + 1] += err5
            if x + 2 < width:
                buf[y, x + 2] += err3
            if y + 1 < height:
                if x - 2 >= 0:
                    buf[y + 1, x - 2] += err2
                if x - 1 >= 0:
                    buf[y + 1, x - 1] += err4
                    
                buf[y + 1, x] += err5
                if x + 1 < width:
                    buf[y + 1, x + 1] += err4
                if x + 2 < width:
                    buf[y + 1, x + 2] += err2
            if y + 2 < height:
                if x - 1 >= 0:
                    buf[y + 2, x - 1] += err2
                buf[y + 2, x] += err3
                if x + 1 < width:
                    buf[y + 2, x + 1] += err2


@njit(cache=True)
def _sierra_lite_kernel(buf, height, width):
    for y in range(height):
        for x in range(width):
            old_val = buf[y, x]
            new_val = 255.0 if old_val > 127.0 else 0.0
            buf[y, x] = new_val
            err = (old_val - new_val) / 10.0
            if err == 0.0:
                continue
            if x + 1 < width:
                buf[y, x + 1] += err * 2.0
            if y + 1 < height:
                if x - 1 >= 0:
                    buf[y + 1, x - 1] += err
                buf[y + 1, x] += err
                if x + 1 < width:
                    buf[y + 1, x + 1] += err


@njit(cache=True)
def _dot_diffusion_inner(buf, order_y, order_x, height, width):
    neigh_dy = np.array([0, 0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2], dtype=np.int32)
    neigh_dx = np.array([1, 2, -2, -1, 0, 1, 2, -2, -1, 0, 1, 2], dtype=np.int32)
    neigh_w  = np.array([4, 2,  2,  4, 4, 4, 2,  1,  2, 2, 2, 1], dtype=np.float32)
    total_weight = 32.0

    for i in range(order_y.shape[0]):
        py = order_y[i]
        px = order_x[i]
        old_val = buf[py, px]
        new_val = 255.0 if old_val > 127.0 else 0.0
        buf[py, px] = new_val
        err = (old_val - new_val) / total_weight
        if err == 0.0:
            continue
        for j in range(12):
            ny = py + neigh_dy[j]
            nx = px + neigh_dx[j]
            if 0 <= ny < height and 0 <= nx < width:
                buf[ny, nx] += err * neigh_w[j]


@njit(cache=True)
def _riemersma_inner(buf, path_x, path_y, height, width):
    n = path_x.shape[0]
    diffusion_count = 4
    error_fraction = 15.0 / 16.0

    for idx in range(n):
        px = path_x[idx]
        py = path_y[idx]
        old_val = buf[py, px]
        new_val = 255.0 if old_val > 127.0 else 0.0
        buf[py, px] = new_val
        err = (old_val - new_val) * error_fraction
        if err == 0.0:
            continue
        share = err / diffusion_count
        for offset in range(1, diffusion_count + 1):
            if idx + offset < n:
                nx = path_x[idx + offset]
                ny = path_y[idx + offset]
                buf[ny, nx] += share


def apply_error_diffusion(gray_img, kernel_name, exporting, rapid_rendering):
    """Applies unrolled error diffusion calculations on grayscale frames using Numba JIT."""
    width, height = gray_img.size

    if kernel_name == "Floyd-Steinberg":
        return gray_img.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")

    buf = np.array(gray_img, dtype=np.float32)

    if kernel_name == "Jarvis-Judice-Ninke":
        _jjn_kernel(buf, height, width)
    elif kernel_name == "Stevenson-Arce":
        _stevenson_arce_kernel(buf, height, width)
    elif kernel_name == "Atkinson":
        _atkinson_kernel(buf, height, width)
    elif kernel_name == "Sierra 3-Row":
        _sierra_3row_kernel(buf, height, width)
    elif kernel_name == "Sierra Lite":
        _sierra_lite_kernel(buf, height, width)
    else:
        return gray_img

    return Image.fromarray(np.where(buf < 128, 0, 255).astype(np.uint8), mode='L')


def apply_dot_diffusion(gray_img, exporting, rapid_rendering):
    """Dot diffusion: processes pixels in a class-matrix order instead of scanline order."""
    width, height = gray_img.size

    key = (width, height)
    cached = _DOTDIFF_CACHE.get(key)
    if cached is None:
        class_matrix = [
            [35, 49, 41, 53, 37, 51, 43, 55],
            [63, 15, 59, 11, 61, 13, 57,  9],
            [31, 47, 39, 51, 33, 45, 37, 49],
            [59,  7, 55,  3, 61, 11, 57,  7],
            [34, 48, 40, 52, 36, 50, 42, 54],
            [62, 14, 58, 10, 60, 12, 56,  8],
            [30, 46, 38, 50, 32, 44, 36, 48],
            [58,  6, 54,  2, 60, 10, 56,  6]
        ]

        order = []
        for cy in range(0, height, 8):
            for cx in range(0, width, 8):
                for by in range(8):
                    for bx in range(8):
                        y, x = cy + by, cx + bx
                        if y < height and x < width:
                            order.append((class_matrix[by][bx], y, x))
        order.sort(key=lambda t: t[0])

        cached = (np.array([t[1] for t in order], dtype=np.int64),
                  np.array([t[2] for t in order], dtype=np.int64))
        _DOTDIFF_CACHE[key] = cached
    order_y, order_x = cached

    buf = np.array(gray_img, dtype=np.float32)
    _dot_diffusion_inner(buf, order_y, order_x, height, width)
    return Image.fromarray(np.where(buf < 128, 0, 255).astype(np.uint8), mode='L')


def apply_riemersma(gray_img, exporting, rapid_rendering):
    """Riemersma dithering: error diffusion along a serpentine path."""
    width, height = gray_img.size

    key = (width, height)
    cached = _RIEMERSMA_CACHE.get(key)
    if cached is None:
        path_x = np.empty(width * height, dtype=np.int64)
        path_y = np.empty(width * height, dtype=np.int64)
        idx = 0
        for y in range(height):
            if y % 2 == 0:
                for x in range(width):
                    path_x[idx] = x
                    path_y[idx] = y
                    idx += 1
            else:
                for x in range(width - 1, -1, -1):
                    path_x[idx] = x
                    path_y[idx] = y
                    idx += 1
        cached = (path_x, path_y)
        _RIEMERSMA_CACHE[key] = cached
    path_x, path_y = cached

    buf = np.array(gray_img, dtype=np.float32)
    _riemersma_inner(buf, path_x, path_y, height, width)
    return Image.fromarray(np.where(buf < 128, 0, 255).astype(np.uint8), mode='L')


def apply_woodcut(gray_img, exporting, rapid_rendering):
    """High-contrast artistic effect: Sobel edges + thresholded original combined."""
    arr = np.array(gray_img, dtype=np.float32)

    padded = np.pad(arr, 1, mode='edge')

    gx = (-padded[:-2, :-2] - 2*padded[1:-1, :-2] - padded[2:, :-2] +
           padded[:-2, 2:] + 2*padded[1:-1, 2:] + padded[2:, 2:])
    gy = (-padded[:-2, :-2] - 2*padded[:-2, 1:-1] - padded[:-2, 2:] +
           padded[2:, :-2] + 2*padded[2:, 1:-1] + padded[2:, 2:])

    edge_mag = np.sqrt(gx**2 + gy**2)

    result = np.where((edge_mag > 40) | (arr <= 128), 0, 255).astype(np.uint8)
    return Image.fromarray(result, mode='L')
