# dither.py
from PIL import Image

def apply_ordered_dither(gray_img, mode, u=-0.25, v=-0.60):
    """Performs structured Bayer ordered dithering or halftone simulation directly on gray frames."""
    gray_copy = gray_img.copy()
    
    if mode == "Flipnote Memory Saver (Experimental)":
        img_256 = gray_copy.resize((256, 192), Image.Resampling.LANCZOS)
        pixels = img_256.load()
        
        brightness_offset = -int(u * 55)
        
        # Vertical Axis (v) controls the dither transition band width
        # At v = -1.0 (bottom), width is 0 (pure high-contrast thresholding)
        # At v = 1.0 (top), width is wide (broad dithered gradients)
        dither_width = int(90 * (v + 1.0))
        dither_width = max(0, min(120, dither_width))
        
        center_threshold = 128
        lower_bound = center_threshold - dither_width
        upper_bound = center_threshold + dither_width
        step = dither_width / 3.0 if dither_width > 0 else 1.0
        
        for y in range(192):
            if y % 2 == 1:
                # Erase alternate scanlines to match classic Flipnote line brush gaps
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
                            # Dark dither (sparse white pixels)
                            pixels[x, y] = 0 if x % 4 != 0 else 255
                        elif val < lower_bound + 2 * step:
                            # Mid dither (alternating checkerboard pattern)
                            pixels[x, y] = 0 if x % 2 == 0 else 255
                        else:
                            # Light dither (sparse black pixels)
                            pixels[x, y] = 0 if x % 4 == 0 else 255
        
        target_w, target_h = gray_copy.size
        img_upscaled = img_256.resize((target_w, target_h), Image.Resampling.BILINEAR)
        return img_upscaled

    if mode == "Bayer 2x2":
        matrix = [
            [0, 2],
            [3, 1]
        ]
        div = 4
        size = 2
    elif mode == "Bayer 3x3":
        matrix = [
            [0, 7, 3],
            [6, 5, 2],
            [4, 1, 8]
        ]
        div = 9
        size = 3
    elif mode == "Bayer 4x4":
        matrix = [
            [ 0,  8,  2, 10],
            [12,  4, 14,  6],
            [ 3, 11,  1,  9],
            [15,  7, 13,  5]
        ]
        div = 16
        size = 4
    elif mode == "Halftone":
        matrix = [
            [12,  8,  4, 11],
            [ 9,  0,  1,  5],
            [13,  3,  2,  6],
            [15, 10,  7, 14]
        ]
        div = 16
        size = 4
    else:  # "Bayer 8x8"
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
        div = 64
        size = 8

    thresh_matrix = [[int((matrix[y][x] / div) * 255) for x in range(size)] for y in range(size)]
    
    width, height = gray_copy.size
    pixels = gray_copy.load()
    for y in range(height):
        for x in range(width):
            old_pixel = pixels[x, y]
            threshold = thresh_matrix[y % size][x % size]
            pixels[x, y] = 255 if old_pixel > threshold else 0
    return gray_copy


def apply_error_diffusion(gray_img, kernel_name, exporting, rapid_rendering):
    """Applies unrolled error diffusion calculations with dynamic downscaling for performance previewing."""
    width, height = gray_img.size

    # Performance optimization during preview playbacks or rapid slider scrub dragging
    if not exporting and rapid_rendering:
        gray_img = gray_img.resize((width // 2, height // 2), Image.Resampling.NEAREST)
        width, height = gray_img.size

    pixels = list(gray_img.getdata())
    buffer = [pixels[i * width:(i + 1) * width] for i in range(height)]
    
    if kernel_name == "Atkinson":
        for y in range(height):
            row = buffer[y]
            row_y1 = buffer[y + 1] if y + 1 < height else None
            row_y2 = buffer[y + 2] if y + 2 < height else None
            for x in range(width):
                old_val = row[x]
                new_val = 255 if old_val > 127 else 0
                row[x] = new_val
                err = (old_val - new_val) / 8
                if err == 0:
                    continue
                if x + 1 < width:
                    row[x + 1] += err
                if x + 2 < width:
                    row[x + 2] += err
                if row_y1:
                    if x - 1 >= 0:
                        row_y1[x - 1] += err
                    row_y1[x] += err
                    if x + 1 < width:
                        row_y1[x + 1] += err
                if row_y2:
                    row_y2[x] += err
                    
    elif kernel_name == "Burkes":
        for y in range(height):
            row = buffer[y]
            row_y1 = buffer[y + 1] if y + 1 < height else None
            for x in range(width):
                old_val = row[x]
                new_val = 255 if old_val > 127 else 0
                row[x] = new_val
                err = (old_val - new_val) / 32
                if err == 0:
                    continue
                err8 = err * 8
                err4 = err * 4
                err2 = err * 2
                if x + 1 < width:
                    row[x + 1] += err8
                if x + 2 < width:
                    row[x + 2] += err4
                if row_y1:
                    if x - 2 >= 0:
                        row_y1[x - 2] += err2
                    if x - 1 >= 0:
                        row_y1[x - 1] += err4
                    row_y1[x] += err8
                    if x + 1 < width:
                        row_y1[x + 1] += err4
                    if x + 2 < width:
                        row_y1[x + 2] += err2

    elif kernel_name == "Jarvis-Judice-Ninke":
        for y in range(height):
            row = buffer[y]
            row_y1 = buffer[y + 1] if y + 1 < height else None
            row_y2 = buffer[y + 2] if y + 2 < height else None
            for x in range(width):
                old_val = row[x]
                new_val = 255 if old_val > 127 else 0
                row[x] = new_val
                err = (old_val - new_val) / 48
                if err == 0:
                    continue
                err7 = err * 7
                err5 = err * 5
                err3 = err * 3
                err1 = err
                if x + 1 < width:
                    row[x + 1] += err7
                if x + 2 < width:
                    row[x + 2] += err5
                    if row_y1:
                        if x - 2 >= 0:
                            row_y1[x - 2] += err3
                        if x - 1 >= 0:
                            row_y1[x - 1] += err5
                        row_y1[x] += err7
                        if x + 1 < width:
                            row_y1[x + 1] += err5
                        if x + 2 < width:
                            row_y1[x + 2] += err3
                if row_y2:
                    if x - 2 >= 0:
                        row_y2[x - 2] += err1
                    row_y2[x] += err4
                    if x + 1 < width:
                        row_y2[x + 1] += err2
                    if x + 2 < width:
                        row_y2[x + 2] += err1

    elif kernel_name == "Stucki":
        for y in range(height):
            row = buffer[y]
            row_y1 = buffer[y + 1] if y + 1 < height else None
            row_y2 = buffer[y + 2] if y + 2 < height else None
            for x in range(width):
                old_val = row[x]
                new_val = 255 if old_val > 127 else 0
                row[x] = new_val
                err = (old_val - new_val) / 42
                if err == 0:
                    continue
                err8 = err * 8
                err4 = err * 4
                err2 = err * 2
                err1 = err
                if x + 1 < width:
                    row[x + 1] += err8
                if x + 2 < width:
                    row[x + 2] += err4
                if row_y1:
                    if x - 2 >= 0:
                        row_y1[x - 2] += err2
                    if x - 1 >= 0:
                        row_y1[x - 1] += err4
                    row_y1[x] += err8
                    if x + 1 < width:
                        row_y1[x + 1] += err4
                    if x + 2 < width:
                        row_y1[x + 2] += err2
                if row_y2:
                    if x - 2 >= 0:
                        row_y2[x - 2] += err1
                    if x - 1 >= 0:
                        row_y2[x - 1] += err2
                    row_y2[x] += err4
                    if x + 1 < width:
                        row_y2[x + 1] += err2
                    if x + 2 < width:
                        row_y2[x + 2] += err1

    elif kernel_name == "Sierra 3-Row":
        for y in range(height):
            row = buffer[y]
            row_y1 = buffer[y + 1] if y + 1 < height else None
            row_y2 = buffer[y + 2] if y + 2 < height else None
            for x in range(width):
                old_val = row[x]
                new_val = 255 if old_val > 127 else 0
                row[x] = new_val
                err = (old_val - new_val) / 32
                if err == 0:
                    continue
                err5 = err * 5
                err4 = err * 4
                err3 = err * 3
                err2 = err * 2
                if x + 1 < width:
                    row[x + 1] += err5
                if x + 2 < width:
                    row[x + 2] += err3
                if row_y1:
                    if x - 2 >= 0:
                        row_y1[x - 2] += err2
                    if x - 1 >= 0:
                        row_y1[x - 1] += err4
                    row_y1[x] += err5
                    if x + 1 < width:
                        row_y1[x + 1] += err4
                    if x + 2 < width:
                        row_y1[x + 2] += err2
                if row_y2:
                    if x - 1 >= 0:
                        row_y2[x - 1] += err2
                    row_y2[x] += err3
                    if x + 1 < width:
                        row_y2[x + 1] += err2

    elif kernel_name == "Sierra 2-Row":
        for y in range(height):
            row = buffer[y]
            row_y1 = buffer[y + 1] if y + 1 < height else None
            for x in range(width):
                old_val = row[x]
                new_val = 255 if old_val > 127 else 0
                row[x] = new_val
                err = (old_val - new_val) / 16
                if err == 0:
                    continue
                err4 = err * 4
                err3 = err * 3
                err2 = err * 2
                err1 = err
                if x + 1 < width:
                    row[x + 1] += err4
                if x + 2 < width:
                    row[x + 2] += err3
                if row_y1:
                    if x - 2 >= 0:
                        row_y1[x - 2] += err1
                    if x - 1 >= 0:
                        row_y1[x - 1] += err2
                    row_y1[x] += err3
                    if x + 1 < width:
                        row_y1[x + 1] += err2
                    if x + 2 < width:
                        row_y1[x + 2] += err1

    elif kernel_name == "Sierra Lite":
        for y in range(height):
            row = buffer[y]
            row_y1 = buffer[y + 1] if y + 1 < height else None
            for x in range(width):
                old_val = row[x]
                new_val = 255 if old_val > 127 else 0
                row[x] = new_val
                err = (old_val - new_val) / 10
                if err == 0:
                    continue
                if x + 1 < width:
                    row[x + 1] += err * 2
                if row_y1:
                    if x - 1 >= 0:
                        row_y1[x - 1] += err
                    row_y1[x] += err
                    if x + 1 < width:
                        row_y1[x + 1] += err

    elif kernel_name == "Stevenson-Arce":
        for y in range(height):
            row = buffer[y]
            row_y1 = buffer[y + 1] if y + 1 < height else None
            row_y2 = buffer[y + 2] if y + 2 < height else None
            row_y3 = buffer[y + 3] if y + 3 < height else None
            for x in range(width):
                old_val = row[x]
                new_val = 255 if old_val > 127 else 0
                row[x] = new_val
                err = (old_val - new_val) / 200
                if err == 0:
                    continue
                if x + 2 < width:
                    row[x + 2] += err * 32
                if row_y1:
                    if x - 3 >= 0:
                        row_y1[x - 3] += err * 12
                    if x - 1 >= 0:
                        row_y1[x - 1] += err * 26
                    if x + 1 < width:
                        row_y1[x + 1] += err * 30
                    if x + 3 < width:
                        row_y1[x + 3] += err * 16
                if row_y2:
                    if x - 2 >= 0:
                        row_y2[x - 2] += err * 12
                    row_y2[x] += err * 26
                    if x + 2 < width:
                        row_y2[x + 2] += err * 12
                if row_y3:
                    if x - 3 >= 0:
                        row_y3[x - 3] += err * 5
                    if x - 1 >= 0:
                        row_y3[x - 1] += err * 12
                    if x + 1 < width:
                        row_y3[x + 1] += err * 12
                    if x + 3 < width:
                        row_y3[x + 3] += err * 5

    flat_data = [max(0, min(255, int(val))) for row in buffer for val in row]
    out_img = Image.new("L", (width, height))
    out_img.putdata(flat_data)

    # Restore frame dimensions via nearest scaling if the preview was downsampled
    if not exporting and rapid_rendering:
        out_img = out_img.resize((width * 2, height * 2), Image.Resampling.NEAREST)

    return out_img
