import os
import sys
import time
import io
import shutil
import json
import queue
import threading
import subprocess
import tempfile
import math
import struct
import re
import numpy as np
import cv2
import pygame
import tkinter as tk
from tkinter import messagebox, filedialog
import customtkinter as ctk
from PIL import Image, ImageTk, ImageEnhance
Image.MAX_IMAGE_PIXELS = 100_000_000

from sf.config import (
    IS_WINDOWS, IS_MAC, IS_LINUX, MAIN_COLOR, SUB_COLOR,
    MAX_FRAMES, SPEED_FPS, load_custom_font, WARNING_DURATION,
    attach_grid_background, nine_slice, GridLabel, symbol_font
)
from sf.about import show_about_dialog
from sf.dither import (apply_ordered_dither, apply_error_diffusion,
                        apply_dot_diffusion, apply_riemersma, apply_woodcut, warmup)

if IS_WINDOWS:
    import ctypes

def _open_in_file_manager(path):
    if not os.path.isdir(path):
        return
    try:
        if IS_WINDOWS:
            os.startfile(path)
        elif IS_MAC:
            subprocess.Popen(["open", "--", path])
        else:
            subprocess.Popen(["xdg-open", "--", path])
    except Exception:
        pass

try:
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

STILL_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
VIDEO_EXTS = (".mp4", ".avi", ".mov", ".mkv", ".gif")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_PREVIEW_MAX_W = 640
_FAST_FORWARD_MAX = 200
_REWIND_TOLERANCE = 2


class FFmpegVideoCapture:
    """Frame source that decodes through the system FFmpeg instead of OpenCV's bundled build.

    OpenCV's Linux wheels ship no software AV1 decoder, so cv2.VideoCapture opens such a
    file, reports a frame count, then fails every read(). FFmpeg is already a hard
    dependency here (export and audio) and is kept current by the OS, so decoding through
    it keeps preview working for every codec the system supports.

    Mimics the sliver of the cv2.VideoCapture API the app uses: get/set on
    CAP_PROP_POS_FRAMES, CAP_PROP_FPS, CAP_PROP_FRAME_COUNT, read() returning a BGR array,
    and release(). Sequential reads stream from one long-lived process; any seek restarts
    FFmpeg at that frame."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.fps, self.frame_count, self.w, self.h = self._probe()
        self.out_w, self.out_h = self._preview_dims()
        self._next = 0
        self._proc = None

    def _probe(self):
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return 0.0, 0, 0, 0
        cmd = [ffprobe, "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height,r_frame_rate,nb_frames:format=duration",
               "-of", "json", self.path]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 creationflags=_NO_WINDOW).stdout
            data = json.loads(out or "{}")
        except Exception:
            return 0.0, 0, 0, 0
        stream = (data.get("streams") or [{}])[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        fps = 0.0
        try:
            num, den = (stream.get("r_frame_rate") or "0/1").split("/")
            if float(den):
                fps = float(num) / float(den)
        except Exception:
            fps = 0.0
        try:
            frame_count = int(stream.get("nb_frames"))
        except (TypeError, ValueError):
            frame_count = 0
        if frame_count <= 0 and fps > 0:
            duration = float((data.get("format") or {}).get("duration") or 0.0)
            frame_count = int(round(duration * fps))
        return fps, frame_count, width, height

    def _preview_dims(self):
        """Source dims capped at _PREVIEW_MAX_W, aspect kept, even for the encoder.

        Computed here rather than left to ffmpeg's scale expressions so the output
        size is known in Python: read() must know exactly how many raw bytes a frame
        is, and a mismatch would desync the pipe."""
        if self.w <= 0 or self.h <= 0 or self.w <= _PREVIEW_MAX_W:
            return self.w, self.h
        out_w = _PREVIEW_MAX_W - (_PREVIEW_MAX_W % 2)
        out_h = int(round(self.h * out_w / self.w))
        out_h -= out_h % 2
        return out_w, max(out_h, 2)

    def isOpened(self):
        return bool(self.w and self.h)

    def _kill(self):
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass
            try:
                self._proc.stdout.close()
            except Exception:
                pass
            self._proc = None

    def _spawn(self):
        self._kill()
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return
        fps = self.fps or 24.0
        args = [ffmpeg, "-loglevel", "error", "-ss", f"{self._next / fps:.6f}",
                "-i", self.path]
        if (self.out_w, self.out_h) != (self.w, self.h):
            args += ["-vf", f"scale={self.out_w}:{self.out_h}"]
        args += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-an", "-sn", "-"]
        self._proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            creationflags=_NO_WINDOW)

    def get(self, prop):
        if prop == cv2.CAP_PROP_POS_FRAMES:
            return float(self._next)
        if prop == cv2.CAP_PROP_FPS:
            return float(self.fps)
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return float(self.frame_count)
        return 0.0

    def _read_exact(self, num_bytes):
        """Pull exactly one frame's worth of bytes off the pipe.

        A frame boundary is only valid if every byte of the frame is consumed, so this
        loops until `num_bytes` are assembled and returns None on EOF. BufferedReader
        normally delivers a full read in one go; assembling here keeps the boundary
        correct even if the stream ever hands back a partial chunk, and it is what lets
        read() distinguish a real end-of-stream from a truncated frame."""
        chunks = []
        remaining = num_bytes
        while remaining > 0:
            chunk = self._proc.stdout.read(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def set(self, prop, value):
        if prop == cv2.CAP_PROP_POS_FRAMES:
            value = max(0, int(value))
            gap = value - self._next
            if self._proc is not None and 0 < gap <= _FAST_FORWARD_MAX:
                self._discard(gap)
            elif self._proc is not None and -_REWIND_TOLERANCE <= gap < 0:
                pass
            elif gap != 0:
                self._next = value
                self._kill()
        return True

    def _discard(self, count):
        """Drop `count` frames off the live pipe without restarting FFmpeg."""
        frame_bytes = self.out_w * self.out_h * 3
        for _ in range(count):
            if self._read_exact(frame_bytes) is None:
                self._kill()
                return
            self._next += 1

    def read(self):
        if not self.isOpened():
            return False, None
        if self._proc is None:
            self._spawn()
            if self._proc is None:
                return False, None
        buf = self._read_exact(self.out_w * self.out_h * 3)
        if buf is None:
            self._kill()
            return False, None
        frame = np.frombuffer(buf, dtype=np.uint8).reshape(self.out_h, self.out_w, 3)
        self._next += 1
        return True, frame

    def release(self):
        self._kill()


def _user_config_dir():
    if IS_WINDOWS:
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "SIGMAFLIP")
    if IS_MAC:
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "SIGMAFLIP")
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config", "SIGMAFLIP")

DSI_JPEG_KEY = bytes.fromhex("70885206DFE5016D45EAC52333D6446F")

_TLS = threading.local()


def _gf_mul2_mod(block: bytes) -> bytes:
    x = int.from_bytes(block, 'little')
    y = (x << 1) & ((1 << 128) - 1)
    if x >> 127:
        y ^= 0x87
    return y.to_bytes(16, 'little')


def _dsi_ccm_tag_mod(data: bytes, nonce: bytes) -> bytes:
    ecb = getattr(_TLS, "ecb", None)
    if ecb is None:
        ecb = AES.new(DSI_JPEG_KEY[::-1], AES.MODE_ECB)
        _TLS.ecb = ecb
        _TLS.zero_enc_rev = ecb.encrypt(b'\x00' * 16)[::-1]

    size = len(data)
    total_size = (size + 15) & ~15
    buf = bytearray(data)
    buf.extend(b'\x00' * (total_size - size))
    buf[0x18A:0x1A6] = b'\x00' * 0x1C

    block = _gf_mul2_mod(_TLS.zero_enc_rev)
    final_bytes = ((size - 1) & 0xF) + 1
    if final_bytes == 0x10:
        block = bytes(a ^ b for a, b in zip(block, bytes(buf[size - 16:size])))
    else:
        tmp = bytearray(16)
        tmp[16 - final_bytes:] = buf[size - final_bytes:size]
        tmp[15 - final_bytes] = 0x80
        block = bytes(a ^ b for a, b in zip(_gf_mul2_mod(block), bytes(tmp)))
    buf[size - final_bytes:size - final_bytes + 16] = block

    b0 = bytes([0x7A]) + nonce[::-1] + b'\x00\x00\x00'
    mac_state = ecb.encrypt(b0)
    for off in range(0, total_size, 16):
        blk = bytes(buf[off:off + 16])[::-1]
        mac_state = ecb.encrypt(bytes(a ^ b for a, b in zip(blk, mac_state)))

    ctr = bytes([2]) + nonce[::-1] + b'\x00\x00\x00'
    s0 = ecb.encrypt(ctr)[::-1]
    return bytes(a ^ b for a, b in zip(mac_state[::-1], s0))


def sign_jpeg_dsi_mod(data: bytes) -> bytes:
    nonce = get_random_bytes(12)
    tag = _dsi_ccm_tag_mod(data, nonce)
    out = bytearray(data)
    out[0x18A:0x18A + 12] = nonce
    out[0x196:0x196 + 16] = tag
    return bytes(out)


def build_dsi_exif_mod(time_str: str, thumb_jpeg: bytes) -> bytes:
    def be16(v):
        return struct.pack(">H", v)
    def be32(v):
        return struct.pack(">I", v)

    ifd0 = bytearray(2 + 9 * 12 + 4)
    ifd0[0:2] = be16(9)
    entries0 = [
        (0x010F, 2, 9, 0x7A), (0x0110, 2, 11, 0x84), (0x011A, 5, 1, 0x90),
        (0x011B, 5, 1, 0x98), (0x0128, 3, 1, 0x00020000), (0x0131, 2, 5, 0xA0),
        (0x0132, 2, 20, 0xA6), (0x0213, 3, 1, 0x00020000), (0x8769, 4, 1, 0xBA),
    ]
    for i, (t, ty, c, v) in enumerate(entries0):
        struct.pack_into(">HHII", ifd0, 2 + i * 12, t, ty, c, v)
    ifd0[2 + 9 * 12:2 + 9 * 12 + 4] = be32(0x1DE)

    sub = bytearray(2 + 10 * 12 + 4)
    sub[0:2] = be16(10)
    entries_sub = [
        (0x9000, 7, 4, 0x30323230), (0x9003, 2, 20, 0x138), (0x9004, 2, 20, 0x14C),
        (0x9101, 7, 4, 0x01020300), (0x927C, 7, 66, 0x160), (0xA000, 7, 4, 0x30303130),
        (0xA001, 3, 1, 0x00010000), (0xA002, 4, 1, 0x280), (0xA003, 4, 1, 0x1E0),
        (0xA005, 4, 1, 0x1A2),
    ]
    for i, (t, ty, c, v) in enumerate(entries_sub):
        struct.pack_into(">HHII", sub, 2 + i * 12, t, ty, c, v)
    sub[2 + 10 * 12:2 + 10 * 12 + 4] = be32(0)

    mn = bytearray(2 + 2 * 12 + 4)
    mn[0:2] = be16(2)
    entries_mn = [(0x1000, 7, 0x1C, 0x17E), (0x1001, 7, 8, 0x19A)]
    for i, (t, ty, c, v) in enumerate(entries_mn):
        struct.pack_into(">HHII", mn, 2 + i * 12, t, ty, c, v)
    mn[2 + 2 * 12:2 + 2 * 12 + 4] = be32(0)

    interop = bytearray(2 + 3 * 12 + 4)
    interop[0:2] = be16(3)
    entries_int = [
        (0x0001, 2, 4, 0x52393800), (0x0002, 7, 4, 0x30313030), (0x1000, 2, 18, 0x1CC),
    ]
    for i, (t, ty, c, v) in enumerate(entries_int):
        struct.pack_into(">HHII", interop, 2 + i * 12, t, ty, c, v)
    interop[2 + 3 * 12:2 + 3 * 12 + 4] = be32(0)

    ifd1 = bytearray(2 + 6 * 12 + 4)
    ifd1[0:2] = be16(6)
    entries_ifd1 = [
        (0x0103, 3, 1, 0x00060000), (0x011A, 5, 1, 0x22C), (0x011B, 5, 1, 0x234),
        (0x0128, 3, 1, 0x00020000), (0x0201, 4, 1, 0x23C), (0x0202, 4, 1, len(thumb_jpeg)),
    ]
    for i, (t, ty, c, v) in enumerate(entries_ifd1):
        struct.pack_into(">HHII", ifd1, 2 + i * 12, t, ty, c, v)
    ifd1[2 + 6 * 12:2 + 6 * 12 + 4] = be32(0)

    dt = time_str.encode() + b'\x00'
    tiff = (
        b"MM\x00\x2A" + be32(8) +
        bytes(ifd0) +
        b"Nintendo\x00\x00" + b"NintendoDS\x00\x00" +
        be32(72) + be32(1) + be32(72) + be32(1) +
        b"EINH\x00\x00" + dt +
        bytes(sub) + dt + dt +
        bytes(mn) +
        b'\x00' * 0x1C + b'\x00' * 8 +
        bytes(interop) + b"JPEG Exif Ver 2.2\x00" +
        bytes(ifd1) +
        be32(72) + be32(1) + be32(72) + be32(1) +
        thumb_jpeg
    )
    if len(tiff) != 0x23C + len(thumb_jpeg):
        raise ValueError("DSi EXIF block size mismatch")
    payload = b"Exif\x00\x00" + tiff
    return be16(len(payload) + 2) + payload


def encode_sign_frame_mod(pil_img: Image.Image, time_str: str, target_path: str, quality: int = 95) -> bool:
    MAX_FILE_SIZE = 140000
    try:
        thumb_buf = io.BytesIO()
        pil_img.resize((160, 120), Image.Resampling.BOX).convert("RGB").save(
            thumb_buf, format="JPEG", quality=70, subsampling=2)
        app1 = b"\xFF\xE1" + build_dsi_exif_mod(time_str, thumb_buf.getvalue())

        signed_data = None
        curr_q = quality
        for _ in range(5):
            main_buf = io.BytesIO()
            pil_img.convert("RGB").save(
                main_buf, format="JPEG", quality=curr_q, subsampling=0, optimize=False)
            body = main_buf.getvalue()[2:]
            signed_data = sign_jpeg_dsi_mod(b"\xFF\xD8" + app1 + body)
            if len(signed_data) <= MAX_FILE_SIZE:
                break
            curr_q -= 10

        with open(target_path, "wb") as f:
            f.write(signed_data)
        return len(signed_data) <= MAX_FILE_SIZE
    except Exception as e:
        print(f"[SIGMAFLIP] Frame encoding failed: {e}")
        return False


_BG_SRC_CACHE = {}
_WATERMARK_CACHE = {}
_CACHE_LOCK = threading.Lock()


def _get_rendered_watermark(wm_path, target_w, scale_pct=40):
    if not wm_path or not os.path.isfile(wm_path):
        return None
    key = (wm_path, target_w, int(scale_pct))
    with _CACHE_LOCK:
        cached = _WATERMARK_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        with Image.open(wm_path) as raw:
            wm = raw.convert("RGBA")
        wm_w = max(16, int(target_w * (scale_pct / 100.0)))
        wm_h = max(1, int(wm.height * (wm_w / wm.width)))
        wm = wm.resize((wm_w, wm_h), Image.Resampling.BILINEAR)
        with _CACHE_LOCK:
            if len(_WATERMARK_CACHE) > 64:
                _WATERMARK_CACHE.clear()
            _WATERMARK_CACHE[key] = wm
        return wm
    except Exception:
        return None


def _load_custom_background_mod(path, size, resample):
    if not path or not os.path.isfile(path):
        return Image.new("RGB", size, "black")
    try:
        with _CACHE_LOCK:
            cached = _BG_SRC_CACHE.get(path)
        if cached is None:
            with Image.open(path) as src:
                src.load()
                src = src.convert("RGB")
                src.thumbnail((640, 480), Image.Resampling.LANCZOS)
            cached = src
            with _CACHE_LOCK:
                _BG_SRC_CACHE[path] = cached
        return cached.resize(size, resample)
    except Exception:
        return Image.new("RGB", size, "black")


def purge_orphaned_export_dirs(max_age_hours=24):
    tmp_root = tempfile.gettempdir()
    cutoff = time.time() - max_age_hours * 3600
    try:
        for name in os.listdir(tmp_root):
            if not name.startswith("sigmaflip_export_"):
                continue
            p = os.path.join(tmp_root, name)
            try:
                if os.path.isdir(p) and (IS_WINDOWS or os.stat(p).st_uid == os.getuid()) \
                        and os.path.getmtime(p) < cutoff:
                    shutil.rmtree(p, ignore_errors=True)
            except OSError:
                pass
    except OSError:
        pass


def _apply_advanced_filters_mod(img, advanced_settings, exporting, playing):
    img = img.convert("RGBA")
    alpha = img.getchannel("A")
    rgb_img = img.convert("RGB")

    contrast_val = advanced_settings.get("contrast", 1.0)
    perf_skip = advanced_settings.get("performance_mode", False) and playing
    if contrast_val != 1.0 and not perf_skip:
        enhancer = ImageEnhance.Contrast(rgb_img)
        rgb_img = enhancer.enhance(contrast_val)

    if advanced_settings.get("black_and_white", False):
        gray_img = rgb_img.convert("L")
        dither = advanced_settings.get("dither_mode", "None")

        if dither == "Floyd-Steinberg":
            bw_img = gray_img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
        elif dither in ("Bayer 2x2", "Bayer 3x3", "Bayer 4x4", "Bayer 8x8",
                        "Blue Noise 64x64",
                        "Halftone", "Flipnote Memory Saver (Experimental)"):
            bw_img = apply_ordered_dither(gray_img, dither)
        elif dither in ("Atkinson", "Jarvis-Judice-Ninke",
                        "Sierra 3-Row", "Sierra Lite",
                        "Stevenson-Arce"):
            bw_img = apply_error_diffusion(gray_img, dither)
        elif dither == "Dot Diffusion":
            bw_img = apply_dot_diffusion(gray_img)
        elif dither == "Riemersma":
            bw_img = apply_riemersma(gray_img)
        elif dither == "Woodcut":
            bw_img = apply_woodcut(gray_img)
        else:
            bw_img = gray_img.point(lambda x: 255 if x > 127 else 0, mode="1")

        if advanced_settings.get("invert_bw", False):
            bw_img = bw_img.convert("L").point(lambda x: 255 - x)
        rgb_img = bw_img.convert("RGB")

    rgb_img.putalpha(alpha)
    return rgb_img


def _apply_scaling_filter_mod(img, target_w, target_h, bg_type, scale_mode, grid_dims,
                              advanced_settings, bg_image_path, exporting, playing,
                              watermark_default_path="", allow_watermark=True):
    orig_w, orig_h = img.size
    pixel_precision = advanced_settings.get("pixel_precision", False)
    perf_mode = advanced_settings.get("performance_mode", False) and playing

    if not pixel_precision and not exporting:
        if perf_mode:
            render_w, render_h = 160, 120
        else:
            render_w, render_h = 320, 240
    else:
        render_w, render_h = target_w, target_h

    if exporting:
        scale_resample = (Image.Resampling.BILINEAR
                          if advanced_settings.get("performance_mode", False)
                          else Image.Resampling.LANCZOS)
    elif perf_mode:
        scale_resample = Image.Resampling.NEAREST
    else:
        scale_resample = Image.Resampling.BOX

    if bg_type == "white":
        bg = Image.new("RGB", (render_w, render_h), "white")
    elif bg_type == "custom":
        bg = _load_custom_background_mod(bg_image_path, (render_w, render_h), scale_resample)
    else:
        bg = Image.new("RGB", (render_w, render_h), "black")

    use_mask = img.mode in ("RGBA", "LA", "PA")
    src_img = img.convert("RGBA") if use_mask else img.convert("RGB")
    cols, rows = grid_dims

    if scale_mode in ("Tiles", "Tiles Stretched"):
        tile_w = render_w // cols
        tile_h = render_h // rows

        if scale_mode == "Tiles Stretched":
            img_copy = src_img.resize((tile_w, tile_h), scale_resample)
            for r in range(rows):
                for col in range(cols):
                    x_offset = col * tile_w
                    y_offset = r * tile_h
                    bg.paste(img_copy, (x_offset, y_offset), mask=img_copy if use_mask else None)
        else:
            scale = min(tile_w / orig_w, tile_h / orig_h)
            new_w = max(1, min(tile_w, int(round(orig_w * scale))))
            new_h = max(1, min(tile_h, int(round(orig_h * scale))))
            img_copy = src_img.resize((new_w, new_h), scale_resample)
            for r in range(rows):
                for col in range(cols):
                    x_offset = col * tile_w + (tile_w - new_w) // 2
                    y_offset = r * tile_h + (tile_h - new_h) // 2
                    bg.paste(img_copy, (x_offset, y_offset), mask=img_copy if use_mask else None)
        bg_final = bg
    elif scale_mode == "Fit":
        scale = min(render_w / orig_w, render_h / orig_h)
        new_w = max(1, min(render_w, int(round(orig_w * scale))))
        new_h = max(1, min(render_h, int(round(orig_h * scale))))
        img_copy = src_img.resize((new_w, new_h), scale_resample)
        bg.paste(img_copy, ((render_w - new_w) // 2, (render_h - new_h) // 2), mask=img_copy if use_mask else None)
        bg_final = bg
    elif scale_mode == "Stretch":
        img_copy = src_img.resize((render_w, render_h), scale_resample)
        bg.paste(img_copy, (0, 0), mask=img_copy if use_mask else None)
        bg_final = bg
    else:
        scale = max(render_w / orig_w, render_h / orig_h)
        new_w = int(orig_w * scale)
        new_h = int(orig_h * scale)
        img_scaled = src_img.resize((new_w, new_h), scale_resample)
        left = (new_w - render_w) // 2
        top = (new_h - render_h) // 2
        img_cropped = img_scaled.crop((left, top, left + render_w, top + render_h))
        bg.paste(img_cropped, (0, 0), mask=img_cropped if use_mask else None)
        bg_final = bg

    if allow_watermark and advanced_settings.get("watermark_enabled", False):
        wm_path = advanced_settings.get("watermark_path") or watermark_default_path
        wm = _get_rendered_watermark(
            wm_path, render_w,
            advanced_settings.get("watermark_scale", 40))
        if wm is not None:
            margin = max(8, int(render_w * 0.02))
            pos = advanced_settings.get("watermark_position", "bottomright")
            positions = {
                "topleft": (margin, margin),
                "topright": (render_w - wm.width - margin, margin),
                "bottomleft": (margin, render_h - wm.height - margin),
                "bottomright": (render_w - wm.width - margin, render_h - wm.height - margin),
            }
            bg_final.paste(wm, positions.get(pos, positions["bottomright"]), wm)

    filtered = _apply_advanced_filters_mod(bg_final, advanced_settings, exporting, playing)

    if render_w != target_w or render_h != target_h:
        filtered = filtered.resize((target_w, target_h), scale_resample)

    return filtered


def _process_and_sign_job(job):
    src_path, dst_path, time_str, params = job
    try:
        with Image.open(src_path) as raw:
            src_img = raw.convert("RGBA")
        processed = _apply_scaling_filter_mod(
            src_img, 640, 480,
            params["bg_type"], params["scale_mode"], params["grid_dims"],
            params["advanced_settings"], params["bg_image_path"],
            True, False,
            params.get("watermark_default_path", ""),
            params.get("allow_watermark", True),
        )
        processed.info.clear()
        return encode_sign_frame_mod(processed, time_str, dst_path)
    except Exception as e:
        print(f"[SIGMAFLIP] Frame processing failed for {os.path.basename(src_path)}: {e}")
        return False


class SIGMAFLIP:
    def __init__(self, root):
        self.root = root
        self.root.title("SIGMAFLIP")
        self.root.geometry("500x620")
        self.root.resizable(False, False)

        self._theme_bg = ("#f3f4f6", "#151515")
        self.root.configure(fg_color=self._theme_bg)

        if not HAS_CRYPTO:
            self.root.withdraw()
            messagebox.showerror(
                "Dependency Missing",
                "SIGMAFLIP requires the 'pycryptodome' library to sign exported JPEGs.\n\n"
                "Please run this command in your terminal or command prompt:\n"
                "pip install pycryptodome\n\n"
                "Then restart the application."
            )
            self.root.destroy()
            sys.exit(1)

        try:
            import numba
        except ImportError:
            print("[SIGMAFLIP] To have a better experience with SIGMAFLIP, please install the 'numba' pip dependency.")

        h = SUB_COLOR.lstrip('#')
        rgb = [int(h[i:i+2], 16) for i in (0, 2, 4)]
        self.highlight_color = "#{:02X}{:02X}{:02X}".format(
            *[int(c + (255 - c) * 0.65) for c in rgb])

        self.base_path = os.path.dirname(os.path.abspath(__file__))
        self.assets_path = os.path.join(self.base_path, 'assets')
        self.img_path = os.path.join(self.assets_path, 'img')
        self.sounds_path = os.path.join(self.assets_path, 'sounds')

        self.validate_assets()

        self.icon_path = os.path.join(self.img_path, 'sigmoid.ico')
        self._set_window_icon(self.root, delay=True)

        font_file = os.path.join(self.assets_path, "font.otf")
        if not os.path.exists(font_file):
            font_file = os.path.join(self.assets_path, "font.ttf")
        self.font_family = load_custom_font(self.root, font_file) if font_file else "Arial"

        self.font_title = ctk.CTkFont(family=self.font_family, size=20, weight="bold")
        self.font_large = ctk.CTkFont(family=self.font_family, size=18)
        self.font_medium = ctk.CTkFont(family=self.font_family, size=16)
        self.font_medium_bold = ctk.CTkFont(family=self.font_family, size=16, weight="bold")
        self.font_small = ctk.CTkFont(family=self.font_family, size=14)
        self.font_tiny = ctk.CTkFont(family=self.font_family, size=12)

        self.main_color_adaptive = ("#1e293b", "#E2E8F0")
        self.sub_color_adaptive = ("#475569", "#94a3b8")
        self.highlight_color_adaptive = ("#3b82f6", self.highlight_color)

        self.video_path = None
        self.cap = None
        self.total_video_frames = 0
        self.video_fps = 24.0
        self.video_duration = 0.0
        self.current_frame_idx = 0.0

        self.image_paths = []
        self.still_index = 0

        self.bg_image_path = None
        self.gif_img = None

        self.temp_audio_path = None
        self.has_audio = False
        self.audio_duration = 0.0
        self._playback_start_time = 0.0
        self._playback_start_frame = 0.0

        self._is_scrubbing = False
        self._was_playing_before_scrub = False

        self.speed = 6
        self.playing = False
        self.scale_mode = "Fit"
        self.after_play_id = None
        self._preview_refresh_id = None
        self.speed_tk_imgs = {}

        self._exporting = False

        self.current_singular_view = "preview"
        self._thumbnail_tk_images = []
        self._tooltip_win = None
        self._rearrange_mode = False
        self._rearrange_grab = False

        config_dir = _user_config_dir()
        os.makedirs(config_dir, exist_ok=True)
        self.config_filepath = os.path.join(config_dir, ".sigmaflip_config.json")
        self._legacy_config_filepath = os.path.join(
            os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".sigmaflip_config.json")

        self.bg_type_var = tk.StringVar(value="black")
        self.export_structure_var = tk.StringVar(value="dcim")
        self.console_type_var = tk.StringVar(value="dsi")
        self.export_mode_var = ctk.StringVar(value="Video Frames")

        self.audio_enabled = True
        self.continuous_export = False
        self.bg_type = "black"
        self.export_structure = "dcim"
        self.console_type = "dsi"

        self.advanced_settings = {
            "pixel_precision": False,
            "black_and_white": False,
            "contrast": 1.0,
            "dither_mode": "None",
            "jpg_quality": 95,
            "album_capacity": 100,
            "pit_dir": "",
            "watermark_enabled": False,
            "watermark_position": "bottomright",
            "watermark_path": "",
            "watermark_scale": 40
        }

        self.sfx_cache = {}
        self._ui_queue = queue.Queue()
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
        except Exception:
            if IS_LINUX:
                try:
                    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
                except Exception:
                    pass

        self.preload_sounds()
        self.preload_tool_assets()
        self.create_menu_bar()
        self.build_ui()
        self.load_user_settings()
        self.redraw_grid()

        self.root.after(1000, lambda: threading.Thread(target=warmup, daemon=True).start())

        self._last_mode = ctk.get_appearance_mode().lower()
        self.poll_appearance_mode()
        self.root.after(80, self._ui_pump)

    def _ui_call(self, fn):
        """Schedule a callable to run on the main (Tk) thread. Thread-safe."""
        self._ui_queue.put(fn)

    def _ui_pump(self):
        """Drains the main-thread dispatch queue. Runs callbacks on the Tk thread."""
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception:
                    sys.excepthook(*sys.exc_info())
        except queue.Empty:
            pass
        try:
            self.root.after(20, self._ui_pump)
        except Exception:
            pass

    def _cleanup_audio(self) -> None:
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass

        if getattr(self, "_temp_audio_dir", None):
            shutil.rmtree(self._temp_audio_dir, ignore_errors=True)
            self._temp_audio_dir = None

        self.temp_audio_path = None
        self.has_audio = False
        self.audio_duration = 0.0

    def validate_assets(self):
        required_images = [
            'play.png', 'pause.png',
            '1.png', '2.png', '3.png', '4.png', '5.png', '6.png', '7.png', '8.png',
            '1_disabled.png', '2_disabled.png', '3_disabled.png', '4_disabled.png',
            '5_disabled.png', '6_disabled.png', '7_disabled.png', '8_disabled.png',
            'prevframe.png', 'prevframe_disabled.png', 'nextframe.png', 'nextframe_disabled.png'
        ]
        missing = []
        if not os.path.exists(self.img_path):
            missing.append(f"Image folder not found at: {self.img_path}")
        else:
            for fname in required_images:
                p = os.path.join(self.img_path, fname)
                if not os.path.exists(p):
                    missing.append(fname)
        if missing:
            self.root.withdraw()
            messagebox.showerror(
                "Missing Assets",
                "SIGMAFLIP cannot start because image assets are missing.\n\n"
                f"Expected directory: {self.img_path}\n"
                f"Missing items:\n" + "\n".join([f" - {item}" for item in missing])
            )
            self.root.destroy()
            sys.exit(1)

    def preload_sounds(self):
        if not os.path.exists(self.sounds_path):
            return
        for f in os.listdir(self.sounds_path):
            if f.lower().endswith(('.mp3', '.wav', '.ogg')):
                try:
                    s = pygame.mixer.Sound(os.path.join(self.sounds_path, f))
                    s.set_volume(0.5)
                    self.sfx_cache[f] = s
                except Exception:
                    pass

    def play_sound(self, filename):
        if filename in self.sfx_cache:
            try:
                now = time.perf_counter()
                throttle = getattr(self, "_sound_throttle", {})
                if now - throttle.get(filename, 0.0) < 0.15:
                    return
                throttle[filename] = now
                self._sound_throttle = throttle
                ch = pygame.mixer.find_channel()
                if ch:
                    ch.play(self.sfx_cache[filename])
            except Exception:
                pass

    SPRITE_HOVER_LIFT = 0.18

    TEXT_FACE_TINT_DOWN = (11, 21, 35)

    FACE_FILES = {"face": "buttonborder.png", "face_down": "buttonborderdown.png"}

    ICON_FILES = [
        ('play', 'play.png', 'play_down.png', 'play_disabled.png'),
        ('pause', 'pause.png', 'pause_down.png', None),
        ('upload', 'upload.png', 'upload_down.png', 'upload_disabled.png'),
        ('prev', 'prevframe.png', 'prevframe_down.png', 'prevframe_disabled.png'),
        ('next', 'nextframe.png', 'nextframe_down.png', 'nextframe_disabled.png'),
        ('beg', 'beg.png', 'beg_down.png', 'beg_disabled.png'),
        ('end', 'end.png', 'end_down.png', 'end_disabled.png'),
        ('lock', 'lock.png', 'lock_down.png', 'lock_disabled.png'),
        ('unlock', 'unlock.png', 'unlock_down.png', 'unlock_disabled.png'),
    ]

    def _load_face(self, fname):
        p = os.path.join(self.img_path, fname)
        return Image.open(p).convert("RGBA") if os.path.exists(p) else None

    def _sprite(self, fname, lift=None):
        """An original button sprite at the row's scale. `lift` blends toward white, which
        is how hover brightens."""
        p = os.path.join(self.img_path, fname)
        if not os.path.exists(p):
            return None
        with Image.open(p) as raw:
            img = raw.convert("RGBA")
        size = (max(1, int(img.width * self.icon_scale)),
                max(1, int(img.height * self.icon_scale)))
        img = img.resize(size, Image.Resampling.LANCZOS)
        if lift:
            img = Image.blend(img, Image.new("RGBA", img.size, (255, 255, 255, 255)), lift)
        return img

    def _face_fill(self, src):
        r, g, b, _ = src.getpixel((src.width // 2, src.height // 2))
        return self._hex((r, g, b))

    @staticmethod
    def _hex(rgb):
        return "#%02X%02X%02X" % tuple(rgb)

    def preload_tool_assets(self):
        self.icon_scale = 0.55
        self.icons = {}
        face = self._load_face(self.FACE_FILES["face"])
        face_down = self._load_face(self.FACE_FILES["face_down"])
        white = Image.new("RGBA", face.size, (255, 255, 255, 255)) if face else None
        face_hover = Image.blend(face, white, self.SPRITE_HOVER_LIFT) if face is not None else None
        self._face_src = {"face": face, "face_down": face_down, "face_hover": face_hover}
        self._face_photos = {}

        for key, base_f, down_f, off_f in self.ICON_FILES:
            states = {
                "": self._sprite(base_f),
                "_hover": self._sprite(base_f, self.SPRITE_HOVER_LIFT),
                "_down": self._sprite(down_f) if down_f else None,
                "_disabled": self._sprite(off_f) if off_f else None,
            }
            states = self._normalize_sprite_states(states)
            for suffix, img in states.items():
                self.icons[key + suffix] = (
                    ctk.CTkImage(light_image=img, dark_image=img, size=img.size) if img else None)

    @staticmethod
    def _pad_sprite(img, w, h):
        """Centre a sprite on a transparent canvas of the given size."""
        if img.width == w and img.height == h:
            return img
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2), img)
        return canvas

    @classmethod
    def _normalize_sprite_states(cls, states):
        """Pads every state of one icon to a single size. CTk grows a button to fit its
        image, so if the pressed sprite is a pixel wider than the idle one the button
        changes width on click; padding each state to the largest removes that."""
        sizes = [(img.width, img.height) for img in states.values() if img]
        if not sizes:
            return states
        w = max(s[0] for s in sizes)
        h = max(s[1] for s in sizes)
        return {suf: (cls._pad_sprite(img, w, h) if img else None)
                for suf, img in states.items()}

    def _raw_bind(self, widget, sequence, func):
        """CTkButton.bind() forwards to _canvas/_text_label/_image_label, but those are
        siblings rather than a chain, and _image_label does not exist yet when a button is
        created without an image. Bind the widget's own window so crossing events from
        anything inside it are caught."""
        tk.Misc.bind(widget, sequence, func, add="+")

    def _swap_face_icon(self, btn, down):
        """down=True shows the pressed sprite; otherwise hover shows the brightened one.
        A disabled button keeps its muted sprite either way."""
        base = getattr(btn, "_sf_base", None)
        if not base:
            return
        disabled = btn.cget("state") == "disabled"
        if down and disabled:
            return
        if disabled:
            key = f"{base}_disabled"
        elif down:
            key = f"{base}_down"
        elif getattr(btn, "_sf_hover", False):
            key = f"{base}_hover"
        else:
            key = base
        img = self.icons.get(key)
        if img is not None and btn.cget("image") is not img:
            btn.configure(image=img)

    def _set_icon(self, btn, base, state=None):
        """The single place a button's sprite is assigned, so hover and press can restore
        it. `base` is always the normal-state key; the disabled sprite is derived.
        Every configure repaints the whole button, so a write is skipped when it would not
        change anything: nav buttons were repainting three times per page move, even when
        their state held, which is the flicker seen going to the next frame."""
        btn._sf_base = base
        if state is not None and btn.cget("state") != state:
            btn.configure(state=state)
        key = f"{base}_disabled" if btn.cget("state") == "disabled" else base
        img = self.icons.get(key)
        if img is not None and btn.cget("image") is not img:
            btn.configure(image=img)
        self._swap_face_icon(btn, False)

    def bind_hover_face(self, btn):
        def hover(event=None):
            btn._sf_hover = True
            self._swap_face_icon(btn, False)

        def leave(event=None):
            btn._sf_hover = False
            self._swap_face_icon(btn, False)

        self._raw_bind(btn, "<Enter>", hover)
        self._raw_bind(btn, "<Leave>", leave)

    def _init_icon_btn(self, btn, base, state=None):
        """Give a freshly built sprite button its base key, press flash and hover."""
        self._set_icon(btn, base, state)
        self.add_press_feedback(btn)
        self.bind_hover_face(btn)

    def add_press_feedback(self, btn):
        """Flashes matching _down variant for ~120ms, then runs the button's command."""
        orig_command = btn.cget("command")

        def flash_click(*args):
            if btn.cget("state") != "normal":
                if orig_command:
                    orig_command()
                return

            down_img = self.icons.get(f"{getattr(btn, '_sf_base', '')}_down")
            if down_img:
                btn.configure(image=down_img)

            def finish_flash():
                if down_img and btn.cget("image") is down_img:
                    self._swap_face_icon(btn, False)
                if orig_command:
                    orig_command()

            btn.after(120, finish_flash)

        btn.configure(command=flash_click)

    def _face_for_state(self, btn, state):
        """Returns the cache key, face art and label colour for the button's state."""
        key = ("face_down" if state["down"]
               else "face_hover" if state["hover"] else "face")
        src = self._face_src.get(key) or self._face_src["face"]
        if src is None:
            return key, None, None
        if state["down"]:
            fg = self._hex(self.TEXT_FACE_TINT_DOWN)
        elif btn._state == "disabled":
            fg = btn._apply_appearance_mode(btn._text_color_disabled)
        else:
            fg = btn._apply_appearance_mode(btn._text_color)
        return key, src, fg

    @staticmethod
    def _patch_text_label_bg(label):
        """CTk resets _text_label's background and foreground on every redraw and on every
        crossing event, and its <Enter>/<Leave> bindings were compiled at __init__, so
        swapping the method afterwards changes nothing. Drop `bg`/`fg` from that label's
        configure so only the caller decides its colours; otherwise the box over the text
        comes back on hover."""
        original = label.configure
        label.configure = lambda *a, **kw: original(
            *a, **{k: v for k, v in kw.items() if k not in ("bg", "fg")})
        return original

    def _bind_face_events(self, btn, state, apply):
        def press(event=None):
            state["down"] = True
            apply()
            if getattr(btn, "_face_reset_id", None):
                btn.after_cancel(btn._face_reset_id)
            btn._face_reset_id = btn.after(150, release)

        def release(event=None):
            btn._face_reset_id = None
            state["down"] = False
            apply()

        def enter(event=None):
            state["hover"] = True
            apply()

        def leave(event=None):
            state["hover"] = False
            apply()

        def moved(event=None):
            pos = (event.widget.winfo_rootx(), event.widget.winfo_rooty()) if event else None
            if pos == state["moved"]:
                return
            state["moved"] = pos
            apply()

        base_draw = btn._draw

        def draw(*args, **kwargs):
            base_draw(*args, **kwargs)
            apply()

        btn._draw = draw
        btn.bind("<Button-1>", press, add="+")
        btn.bind("<ButtonRelease-1>", release, add="+")
        btn.bind("<Configure>", apply, add="+")
        self._raw_bind(btn, "<Enter>", enter)
        self._raw_bind(btn, "<Leave>", leave)
        self._raw_bind(btn, "<Configure>", moved)
        btn.after(80, apply)
        btn._sf_face = True

    def stamp_text_face(self, btn):
        """Text buttons have no image slot, so the border art goes on their own canvas.
        buttonborderdown is the pressed face; hover lifts the face toward white."""
        state = {"down": False, "hover": False, "moved": False}
        set_label_bg = self._patch_text_label_bg(btn._text_label)

        def apply(event=None):
            c = getattr(btn, "_canvas", None)
            w, h = btn.winfo_width(), btn.winfo_height()
            if c is None or w <= 1 or h <= 1:
                return
            key, src, fg = self._face_for_state(btn, state)
            if src is None:
                return
            pkey = (key, w, h)
            photo = self._face_photos.get(pkey)
            if photo is None:
                photo = ImageTk.PhotoImage(nine_slice(src, (w, h)))
                self._face_photos[pkey] = photo
            set_label_bg(bg=self._face_fill(src), fg=fg)
            c.delete("face")
            c.create_image(0, 0, image=photo, anchor="nw", tags="face")
            if c.find_withtag("border_parts"):
                c.tag_raise("border_parts")

        self._bind_face_events(btn, state, apply)

    def _set_window_icon(self, window, delay=True):
        """Prefers the .ico over the .png for the taskbar icon."""
        ico_path = os.path.join(self.img_path, 'sigmoid.ico')
        png_path = os.path.join(self.img_path, 'sigmoid.png')

        def do_set():
            try:
                if IS_WINDOWS and os.path.exists(ico_path):
                    try:
                        window.iconbitmap(ico_path)
                    except Exception as e:
                        print(f"Windows iconbitmap configuration error: {e}")

                best_icon_path = ico_path if os.path.exists(ico_path) else (png_path if os.path.exists(png_path) else None)
                if best_icon_path:
                    img = Image.open(best_icon_path)
                    photo = ImageTk.PhotoImage(img)
                    window.iconphoto(True, photo)
                    window._icon_img = photo
            except Exception as e:
                print(f"Icon configuration error: {e}")

        if delay:
            window.after(250, do_set)
        else:
            do_set()

    def create_menu_bar(self):
        menu_font = (self.font_family, -12)

        self.menubar = tk.Menu(self.root, font=menu_font)
        self.root.configure(menu=self.menubar)

        self.options_menu = tk.Menu(self.menubar, tearoff=0, font=menu_font)
        self.menubar.add_cascade(label="Options", menu=self.options_menu)

        self.audio_menu_var = tk.BooleanVar(value=self.audio_enabled)
        self.options_menu.add_checkbutton(label="Enable Audio Preview",
                                          variable=self.audio_menu_var,
                                          command=self.toggle_audio_menu_item)

        self.options_menu.add_separator()

        self.bg_menu = tk.Menu(self.options_menu, tearoff=0, font=menu_font)
        self.options_menu.add_cascade(label="Background Setting", menu=self.bg_menu)

        self.bg_menu.add_radiobutton(label="Solid Black", variable=self.bg_type_var, value="black",
                                     command=lambda: self.set_bg_menu_type("black"))
        self.bg_menu.add_radiobutton(label="Solid White", variable=self.bg_type_var, value="white",
                                     command=lambda: self.set_bg_menu_type("white"))
        self.bg_menu.add_radiobutton(label="Custom Background...", variable=self.bg_type_var, value="custom",
                                     command=lambda: self.set_bg_menu_type("custom"))

        self.options_menu.add_separator()

        self.struct_menu = tk.Menu(self.options_menu, tearoff=0, font=menu_font)
        self.options_menu.add_cascade(label="Export Folder Structure", menu=self.struct_menu)

        self.struct_menu.add_radiobutton(label="Native DCIM (10XNIN01/NIN02)", variable=self.export_structure_var, value="dcim",
                                         command=lambda: self.set_struct_menu_type("dcim"))
        self.struct_menu.add_radiobutton(label="Sequential Parts (SFPart_X)", variable=self.export_structure_var, value="parts",
                                         command=lambda: self.set_struct_menu_type("parts"))

        self.options_menu.add_separator()

        self.console_menu = tk.Menu(self.options_menu, tearoff=0, font=menu_font)
        self.options_menu.add_cascade(label="Target Console", menu=self.console_menu)

        self.console_menu.add_radiobutton(label="Nintendo DSi", variable=self.console_type_var, value="dsi",
                                          command=lambda: self.set_console_type("dsi"))
        self.console_menu.add_radiobutton(label="Nintendo 3DS", variable=self.console_type_var, value="3ds",
                                          command=lambda: self.set_console_type("3ds"))

        self.options_menu.add_separator()

        self.continuous_export_var = tk.BooleanVar(value=self.continuous_export)
        self.options_menu.add_checkbutton(label="Continuous Export",
                                          variable=self.continuous_export_var,
                                          command=self.toggle_continuous_export_menu_item)

        self.options_menu.add_command(label="Advanced Settings...", command=self.show_advanced_settings)
        self.adv_menu_index = self.options_menu.index("end")

        self.menubar.add_command(label="Keyboard Shortcuts", command=self.show_keybinds)
        self.menubar.add_command(label="About", command=self.show_about_dialog)

        self._theme_menus()

    def _theme_menus(self):
        dark = ctk.get_appearance_mode().lower() == "dark"
        klof = dict(
            bg="#1f1f1f" if dark else "#ffffff",
            fg="#E8E8E8" if dark else "#111827",
            activebackground="#3d4a5d" if dark else "#e2e8f0",
            activeforeground="#ffffff" if dark else "#111827",
            borderwidth=1)
        klof["selectcolor"] = "#d0d0d0" if dark else "#111827"
        for menu in (self.menubar, self.options_menu,
                     self.bg_menu, self.struct_menu, self.console_menu):
            try:
                menu.configure(font=(self.font_family, -12), **klof)
            except Exception:
                pass

    def toggle_audio_menu_item(self):
        self.audio_enabled = self.audio_menu_var.get()
        self.on_audio_toggle()

    def toggle_continuous_export_menu_item(self):
        self.continuous_export = self.continuous_export_var.get()
        self.play_sound('apply.mp3')

    def set_bg_menu_type(self, bg_type, silent=False):
        if bg_type == "custom":
            if silent:
                if self.bg_image_path and os.path.isfile(self.bg_image_path):
                    self.bg_type = "custom"
                    self.bg_type_var.set("custom")
                else:
                    self.bg_image_path = None
                    self.bg_type = "black"
                    self.bg_type_var.set("black")
            else:
                self.select_custom_bg_image()
                if self.bg_type_var.get() != "custom":
                    return
        else:
            self.bg_type = bg_type
            self.bg_type_var.set(bg_type)
            if not silent:
                self.on_bg_type_change()

    def set_struct_menu_type(self, struct_type, silent=False):
        self.export_structure = struct_type
        self.export_structure_var.set(struct_type)
        if not silent:
            self.on_struct_type_change()

    def set_console_type(self, console_type):
        self.console_type = console_type
        self.console_type_var.set(console_type)

    def _prompt_pit_deletion(self, pit):
        if threading.current_thread() is threading.main_thread():
            return messagebox.askyesno(
                "Delete album cache?",
                f"A stale photo album cache was found at:\n{pit}\n\n"
                "Delete it now so the console re-scans the new photos?")
        result = [False]
        done = threading.Event()
        def ask():
            try:
                result[0] = messagebox.askyesno(
                    "Delete album cache?",
                    f"A stale photo album cache was found at:\n{pit}\n\n"
                    "Delete it now so the console re-scans the new photos?")
            finally:
                done.set()
        self._ui_call(ask)
        if not done.wait(30):
            return False
        return result[0]

    def _cleanup_dsi_album_cache(self, export_dir):
        pit = os.path.join(export_dir, "private", "ds", "app", "484E494A", "pit.bin")
        if not os.path.isfile(pit):
            return ""
        if not self._prompt_pit_deletion(pit):
            return ""
        try:
            os.unlink(pit)
        except Exception:
            return ""
        return f"\nDeleted stale album cache: {pit}"

    def _validate_still_image(self, file_path):
        try:
            if not file_path.lower().endswith(STILL_IMAGE_EXTS):
                raise ValueError("File format not supported in Still Images mode.")
            with Image.open(file_path) as img:
                img.verify()
            with Image.open(file_path) as img:
                if getattr(img, 'n_frames', 1) > 1:
                    raise ValueError("Animated image isn't supported. Convert it to a video or GIF first, then use Video Frames mode instead.")
                width, height = img.size
                if width < 1 or height < 1:
                    raise ValueError("Image has invalid dimensions.")
                if width * height > 100_000_000:
                    raise ValueError("Image is too large to safely process (over 100 megapixels).")
            return True, ""
        except Exception as e:
            return False, str(e)

    def select_custom_bg_image(self):
        file_path = filedialog.askopenfilename(
            title="Select Custom Background Frame Image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.webp")]
        )
        if file_path:
            valid, error = self._validate_still_image(file_path)
            if not valid:
                self.play_sound('warning.mp3')
                messagebox.showerror(
                    "Invalid Background",
                    f"That image could not be safely loaded:\n\n{error}\n\n"
                    "Your current background setting has been kept unchanged."
                )
                return
            with _CACHE_LOCK:
                _BG_SRC_CACHE.clear()
            self.bg_image_path = file_path
            self.bg_type = "custom"
            self.bg_type_var.set("custom")
            self.play_sound('apply.mp3')
            self.update_frame_display()
        else:
            self.play_sound('back.mp3')
            if not self.bg_image_path:
                self.bg_type = "black"
                self.bg_type_var.set("black")
            else:
                self.bg_type = "custom"
                self.bg_type_var.set("custom")

    def on_bg_type_change(self):
        self.play_sound('apply.mp3')
        self.update_frame_display()

    def on_struct_type_change(self):
        self.play_sound('apply.mp3')
        if self.cap or self.image_paths:
            self.check_timing_warnings(show_popup=False)

    def show_about_dialog(self):
        fonts = {
            'title': self.font_title,
            'large': self.font_large,
            'medium': self.font_medium,
            'small': self.font_small,
            'tiny': self.font_tiny
        }
        show_about_dialog(
            parent=self.root,
            fonts=fonts,
            icon_path=self.icon_path,
            main_color=self.main_color_adaptive,
            sub_color=self.sub_color_adaptive,
            set_icon_fn=self._set_window_icon
        )

    def show_advanced_settings(self):
        if getattr(self, "_adv_win", None) is not None and self._adv_win.winfo_exists():
            self._adv_win.lift()
            self._adv_win.focus_force()
            return
        self.play_sound('apply.mp3')
        fonts = {
            'title': self.font_title,
            'large': self.font_large,
            'medium': self.font_medium,
            'medium_bold': self.font_medium_bold,
            'small': self.font_small,
            'tiny': self.font_tiny
        }
        from sf.advanced import show_advanced_dialog
        self._adv_win = show_advanced_dialog(
            parent=self.root,
            fonts=fonts,
            main_color=self.main_color_adaptive,
            sub_color=self.sub_color_adaptive,
            highlight_color=self.highlight_color_adaptive,
            settings=self.advanced_settings,
            on_change_callback=self.request_preview_refresh,
            get_export_structure=lambda: self.export_structure,
            get_console_type=lambda: self.console_type,
            set_icon_fn=self._set_window_icon,
            theme_bg=self._theme_bg,
            get_exporting=lambda: getattr(self, "_exporting", False),
            get_export_mode=lambda: self.export_mode_var.get()
        )

    def on_audio_toggle(self):
        self.play_sound('apply.mp3')
        if self.audio_enabled:
            if self.video_path and not self.has_audio and "Video" in self.export_mode_var.get():
                threading.Thread(target=self.extract_audio_thread, daemon=True).start()
        else:
            self._cleanup_audio()

    def poll_appearance_mode(self):
        try:
            curr_mode = ctk.get_appearance_mode().lower()
            if self._last_mode != curr_mode:
                self._last_mode = curr_mode
                self.on_appearance_mode_changed(curr_mode)
        except Exception:
            pass
        self.root.after(2000, self.poll_appearance_mode)

    def on_appearance_mode_changed(self, new_mode):
        try:
            self._theme_menus()
            self.redraw_grid()
            adv_redraw = getattr(getattr(self, "_adv_win", None), "_adv_grid_redraw", None)
            if adv_redraw is not None:
                adv_redraw()
            if self.current_singular_view == "grid" and self.image_paths:
                self.root.after(100, self.populate_thumbnail_grid)
        except Exception:
            pass

    def build_ui(self):
        self.redraw_grid = attach_grid_background(self.root)
        self.root.bind_all("<Key>", self.on_global_key)

        top_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        top_frame.pack(fill="x", pady=5)

        self.title_label = GridLabel(
            top_frame, text="SIGMAFLIP", font=self.font_title, text_color=self.main_color_adaptive, fg_color="transparent"
        )
        self.title_label.pack()

        self.file_name_label = GridLabel(
            top_frame, text="No Video Loaded", font=self.font_tiny, text_color=self.sub_color_adaptive, fg_color="transparent"
        )
        self.file_name_label.pack()

        self.preview_frame = ctk.CTkFrame(self.preview_frame if hasattr(self, 'preview_frame') else self.root, width=320, height=240, fg_color="black", border_width=2, border_color=("#cbd5e1", "#E2E8F0"))
        self.preview_frame.pack(pady=5)
        self.preview_frame.pack_propagate(False)

        self.video_canvas = tk.Canvas(self.preview_frame, bg="black", highlightthickness=0)
        self.video_canvas.pack(fill="both", expand=True)

        self.grid_scroll_frame = ctk.CTkScrollableFrame(
            self.preview_frame, width=320, height=240, fg_color="black", corner_radius=0
        )

        self.toggle_view_btn = ctk.CTkButton(
            self.root, text="Switch to Grid View", command=self.toggle_singular_view_mode,
            width=150, height=32, font=self.font_tiny,
            text_color=self.main_color_adaptive
        )
        self.stamp_text_face(self.toggle_view_btn)

        self.grid_controls_row = ctk.CTkFrame(self.root, fg_color="transparent")
        self.delete_frame_btn = ctk.CTkButton(
            self.grid_controls_row, text="Delete Selected", command=self.delete_selected_frame,
            width=132, height=32, font=self.font_tiny,
            text_color=self.main_color_adaptive
        )
        self.delete_frame_btn.pack(side=tk.LEFT, padx=5)
        self.stamp_text_face(self.delete_frame_btn)

        self.move_left_btn = ctk.CTkButton(
            self.grid_controls_row, text="Move Left", command=self.move_frame_left,
            width=92, height=32, font=self.font_tiny,
            text_color=self.main_color_adaptive
        )
        self.move_left_btn.pack(side=tk.LEFT, padx=5)
        self.stamp_text_face(self.move_left_btn)

        self.move_right_btn = ctk.CTkButton(
            self.grid_controls_row, text="Move Right", command=self.move_frame_right,
            width=92, height=32, font=self.font_tiny,
            text_color=self.main_color_adaptive
        )
        self.move_right_btn.pack(side=tk.LEFT, padx=5)
        self.stamp_text_face(self.move_right_btn)

        self.timeline_slider = ctk.CTkSlider(
            self.root, from_=0, to=100, number_of_steps=100,
            button_color=self.main_color_adaptive, button_hover_color=SUB_COLOR,
            progress_color=self.main_color_adaptive, command=self.on_slider_scrub, bg_color="transparent"
        )
        self.timeline_slider.pack(fill="x", padx=40, pady=5)
        self.timeline_slider.set(0)

        self.timeline_slider.bind("<Button-1>", self.on_slider_press, add="+")
        self.timeline_slider.bind("<ButtonRelease-1>", self.on_slider_release, add="+")
        if hasattr(self.timeline_slider, "_canvas"):
            self.timeline_slider._canvas.bind("<Button-1>", self.on_slider_press, add="+")
            self.timeline_slider._canvas.bind("<ButtonRelease-1>", self.on_slider_release, add="+")

        self.nav_row = ctk.CTkFrame(self.root, fg_color="transparent")
        btn_defaults = dict(hover_color=self._theme_bg, corner_radius=8, fg_color="transparent")

        self.beg_btn = ctk.CTkButton(
            self.nav_row, text="", width=40, height=36,
            command=self.jump_to_beginning, **btn_defaults
        )
        self.beg_btn.pack(side=tk.LEFT, padx=5)
        self._init_icon_btn(self.beg_btn, 'beg')

        self.prev_btn = ctk.CTkButton(
            self.nav_row, text="", width=40, height=36,
            command=self.show_prev_image, **btn_defaults
        )
        self.prev_btn.pack(side=tk.LEFT, padx=5)
        self._init_icon_btn(self.prev_btn, 'prev')

        self.nav_label = GridLabel(
            self.nav_row, text="0 of 0", font=self.font_small, text_color=self.sub_color_adaptive, fg_color="transparent"
        )
        self.nav_label.pack(side=tk.LEFT, padx=5)

        self.next_btn = ctk.CTkButton(
            self.nav_row, text="", width=40, height=36,
            command=self.show_next_image, **btn_defaults
        )
        self.next_btn.pack(side=tk.LEFT, padx=5)
        self._init_icon_btn(self.next_btn, 'next')

        self.end_btn = ctk.CTkButton(
            self.nav_row, text="", width=40, height=36,
            command=self.jump_to_end, **btn_defaults
        )
        self.end_btn.pack(side=tk.LEFT, padx=5)
        self._init_icon_btn(self.end_btn, 'end')

        self.tile_config_row = ctk.CTkFrame(self.root, fg_color="transparent")
        self.tile_frame_label = GridLabel(
            self.tile_config_row, text="Tile Size:",
            font=self.font_small, text_color=self.sub_color_adaptive, fg_color="transparent"
        )
        self.tile_frame_label.pack(side=tk.LEFT, padx=(0, 5))

        self.tile_cols_entry = ctk.CTkEntry(
            self.tile_config_row, width=45, height=28,
            font=self.font_tiny, justify="center", border_color=self.main_color_adaptive
        )
        self.tile_cols_entry.insert(0, "2")
        self.tile_cols_entry.pack(side=tk.LEFT, padx=5)
        self.tile_cols_entry.bind("<KeyRelease>", self.on_grid_entry_change)

        self.tile_x_label = GridLabel(
            self.tile_config_row, text="x",
            font=self.font_small, text_color=self.sub_color_adaptive, fg_color="transparent"
        )
        self.tile_x_label.pack(side=tk.LEFT, padx=2)

        self.tile_rows_entry = ctk.CTkEntry(
            self.tile_config_row, width=45, height=28,
            font=self.font_tiny, justify="center", border_color=self.main_color_adaptive
        )
        self.tile_rows_entry.insert(0, "2")
        self.tile_rows_entry.pack(side=tk.LEFT, padx=5)

        self.tile_link_locked = False
        self.tile_link_btn = ctk.CTkButton(
            self.tile_config_row, text="", width=32, height=30,
            command=self.toggle_tile_link,
            fg_color="transparent", hover_color=self._theme_bg,
            image=self.icons.get('unlock'), corner_radius=4
        )
        self.tile_link_btn.pack(side=tk.LEFT, padx=(5, 0))
        self._init_icon_btn(self.tile_link_btn, 'unlock')
        self.tile_rows_entry.bind("<KeyRelease>", self.on_grid_entry_change)

        self.limit_indicator = GridLabel(
            self.root, text="Export Frames: 0 / 999", font=self.font_small, text_color=self.sub_color_adaptive, fg_color="transparent"
        )
        self.limit_indicator.pack(pady=3)

        self.sf_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        self.sf_frame.pack(pady=4)

        sample_path = os.path.join(self.img_path, "1.png")
        self.speed_w, self.speed_h = (180, 40)
        if os.path.exists(sample_path):
            img_sample = Image.open(sample_path)
            self.speed_w = img_sample.width
            self.speed_h = img_sample.height

        self.speed_widget = tk.Canvas(self.sf_frame, width=self.speed_w, height=self.speed_h,
                                      bg=("#f3f4f6" if ctk.get_appearance_mode().lower() == "light" else "#151515"), highlightthickness=0, bd=0, cursor="hand2")
        self.speed_widget.pack(pady=2)
        self.speed_widget.bind("<Button-1>", self.on_speed_widget_click)
        self.sync_speed_widget_image()

        self.config_row = ctk.CTkFrame(self.root, fg_color="transparent")
        self.config_row.pack(pady=4, fill="x", padx=45)

        GridLabel(self.config_row, text="Resize:", font=self.font_small, text_color=self.sub_color_adaptive, fg_color="transparent").pack(side=tk.LEFT, padx=(0, 5))

        self.aspect_menu = ctk.CTkOptionMenu(
            self.config_row, values=["Fit (Letterbox)", "Stretch", "Crop (4:3)", "Tiles", "Tiles Stretched"],
            command=self.set_scale_mode,
            fg_color=("#e2e8f0", "#2b2b2b"),
            button_color=("#cbd5e1", "#334155"),
            button_hover_color=self.highlight_color_adaptive,
            dropdown_fg_color=("#ffffff", "#2b2b2b"),
            dropdown_hover_color=self.highlight_color_adaptive,
            dropdown_text_color=("#1e293b", "#E2E8F0"),
            text_color=self.main_color_adaptive,
            font=self.font_tiny, dropdown_font=self.font_tiny,
            width=110
        )
        self.aspect_menu.pack(side=tk.LEFT)

        GridLabel(self.config_row, text="Mode:", font=self.font_small, text_color=self.sub_color_adaptive, fg_color="transparent").pack(side=tk.LEFT, padx=(15, 5))
        self.export_mode_var = ctk.StringVar(value="Video Frames")
        self.export_mode_menu = ctk.CTkOptionMenu(
            self.config_row, values=["Video Frames", "Still Images"],
            variable=self.export_mode_var,
            fg_color=("#e2e8f0", "#2b2b2b"),
            button_color=("#cbd5e1", "#334155"),
            button_hover_color=self.highlight_color_adaptive,
            dropdown_fg_color=("#ffffff", "#2b2b2b"),
            dropdown_hover_color=self.highlight_color_adaptive,
            dropdown_text_color=("#1e293b", "#E2E8F0"),
            text_color=self.main_color_adaptive,
            font=self.font_tiny, dropdown_font=self.font_tiny,
            width=140, command=self.on_export_mode_change
        )
        self.export_mode_menu.pack(side=tk.LEFT)

        self.ctrl_row = ctk.CTkFrame(self.root, fg_color="transparent")
        self.ctrl_row.pack(pady=5)

        self.load_btn = ctk.CTkButton(
            self.ctrl_row, text="", command=self.load_video_dialog,
            width=100, height=36, image=self.icons.get('upload'),
            fg_color="transparent", text_color=self.main_color_adaptive, hover_color=self._theme_bg, corner_radius=8
        )
        self.load_btn.pack(side=tk.LEFT, padx=3)
        self._init_icon_btn(self.load_btn, 'upload')

        self.play_btn = ctk.CTkButton(
            self.ctrl_row, text="", command=self.toggle_play,
            width=100, height=36, image=self.icons.get('play_disabled'), state="disabled",
            fg_color="transparent", text_color=self.main_color_adaptive, hover_color=self._theme_bg, corner_radius=8
        )
        self.play_btn.pack(side=tk.LEFT, padx=3)
        self._init_icon_btn(self.play_btn, 'play', 'disabled')

        self.export_btn = ctk.CTkButton(
            self.ctrl_row, text="Export Frames", command=self.export_frames,
            width=100, height=38, font=self.font_medium_bold, state="disabled",
            text_color=self.main_color_adaptive,
            corner_radius=8
        )
        self.export_btn.pack(side=tk.LEFT, padx=3)
        self.stamp_text_face(self.export_btn)

        self.progress_bar = ctk.CTkProgressBar(self.root, progress_color=self.highlight_color_adaptive, height=8)
        self.progress_bar.pack(fill="x", padx=45, pady=8)
        self.progress_bar.set(0)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def toggle_tile_link(self):
        self.tile_link_locked = not self.tile_link_locked
        if self.tile_link_locked:
            val = self.tile_cols_entry.get()
            self.tile_rows_entry.delete(0, tk.END)
            self.tile_rows_entry.insert(0, val)
            self.tile_x_label.pack_forget()
            self.tile_rows_entry.pack_forget()
            self._set_icon(self.tile_link_btn, 'lock')
            self.tile_link_btn.configure(fg_color=self.highlight_color_adaptive)
            self.play_sound('lock.mp3')
        else:
            val = self.tile_cols_entry.get()
            self.tile_rows_entry.delete(0, tk.END)
            self.tile_rows_entry.insert(0, val)
            self.tile_x_label.pack(side=tk.LEFT, padx=2, before=self.tile_link_btn)
            self.tile_rows_entry.pack(side=tk.LEFT, padx=5, before=self.tile_link_btn)
            self._set_icon(self.tile_link_btn, 'unlock')
            self.tile_link_btn.configure(fg_color="transparent")
            self.play_sound('unlock.mp3')
        self.on_grid_entry_change()

    def on_grid_entry_change(self, event=None):
        if self.tile_link_locked:
            val = self.tile_cols_entry.get()
            self.tile_rows_entry.delete(0, tk.END)
            self.tile_rows_entry.insert(0, val)
        if self.cap or self.image_paths:
            self.check_timing_warnings(show_popup=False)
        self.update_frame_display()

    def get_grid_dimensions(self):
        try:
            cols = int(self.tile_cols_entry.get())
            if cols < 1:
                cols = 1
        except ValueError:
            cols = 2

        try:
            rows = int(self.tile_rows_entry.get())
            if rows < 1:
                rows = 1
        except ValueError:
            rows = 2

        if cols == 1 and rows == 1:
            cols = 2
            rows = 2

        return cols, rows

    def sync_speed_widget_image(self, force_enabled=None):
        self.speed_widget.delete("speed_img")
        if force_enabled is None:
            is_enabled = (not self.playing) and (not getattr(self, "_exporting", False))
        else:
            is_enabled = force_enabled
        p_img = f"{self.speed}.png" if is_enabled else f"{self.speed}_disabled.png"
        p = os.path.join(self.img_path, p_img)
        if os.path.exists(p):
            img = Image.open(p)
            self.speed_tk_imgs[self.speed] = ImageTk.PhotoImage(img)
            self.speed_widget.create_image(0, 0, image=self.speed_tk_imgs[self.speed], anchor="nw", tags="speed_img")

        curr_mode = ctk.get_appearance_mode().lower()
        bg_color = "#2b2b2b" if curr_mode == "dark" else "#ebebeb"
        self.speed_widget.configure(bg=bg_color)

    def on_speed_widget_click(self, event):
        if getattr(self, "_exporting", False) or self.playing:
            return
        x = event.x
        adjusted_x = x + 4
        if adjusted_x < 44:
            return
        arrow_area_width = self.speed_w - 44
        fraction = (adjusted_x - 44) / arrow_area_width
        clicked_speed = int(fraction * 8) + 1
        clicked_speed = max(1, min(clicked_speed, 8))
        self.set_flipnote_speed(clicked_speed)

    def set_flipnote_speed(self, speed_idx):
        speed_idx = max(1, min(speed_idx, 8))
        if speed_idx == self.speed:
            return
        self.speed = speed_idx
        self.sync_speed_widget_image()
        self.play_sound(f'speed{self.speed}.mp3')
        if self.cap:
            self.check_timing_warnings(show_popup=False)

    def toggle_singular_view_mode(self):
        self.play_sound('apply.mp3')
        if self.current_singular_view == "preview":
            self.switch_to_grid_view()
        else:
            self.switch_to_preview_view()

    def switch_to_grid_view(self):
        self.current_singular_view = "grid"
        self.toggle_view_btn.configure(text="Switch to Preview View")
        self.video_canvas.pack_forget()
        self.grid_scroll_frame.pack(fill="both", expand=True)
        self.grid_controls_row.pack(pady=5)
        self.populate_thumbnail_grid()
        self.repack_singular_image_layout()

    def switch_to_preview_view(self):
        self.exit_rearrange_mode()
        self.current_singular_view = "preview"
        self.toggle_view_btn.configure(text="Switch to Grid View")
        self._stop_thumb_scroll()
        self.grid_scroll_frame.pack_forget()
        self.grid_controls_row.pack_forget()
        self.video_canvas.pack(fill="both", expand=True)
        self.update_frame_display()
        self.repack_singular_image_layout()

    def single_click_grid_image(self, index):
        self.play_sound('apply.mp3')
        self.hide_grid_tooltip()
        old_idx = self.still_index
        self.still_index = index
        self.video_path = self.image_paths[self.still_index]

        if len(self.image_paths) == 1:
            self.file_name_label.configure(text=os.path.basename(self.video_path))
        else:
            self.file_name_label.configure(text=f"{len(self.image_paths)} Images Loaded")

        self._update_thumb_selection(old_idx, index)
        self.update_nav_buttons_state()

    def show_grid_tooltip(self, event, filename):
        self.hide_grid_tooltip()
        self._tooltip_win = tk.Toplevel(self.root)
        self._tooltip_win.wm_overrideredirect(True)
        x = event.x_root + 15
        y = event.y_root + 10
        self._tooltip_win.wm_geometry(f"+{x}+{y}")

        if ctk.get_appearance_mode().lower() == "light":
            tip_bg, tip_fg = "#e5e7eb", "#1e293b"
        else:
            tip_bg, tip_fg = "#2d2d2d", "#E2E8F0"

        lbl = tk.Label(
            self._tooltip_win,
            text=filename,
            font=(self.font_family, 9),
            bg=tip_bg,
            fg=tip_fg,
            padx=6,
            pady=4,
            relief="solid",
            bd=1,
            highlightthickness=0
        )
        lbl.pack()

    def hide_grid_tooltip(self, event=None):
        if hasattr(self, "_tooltip_win") and self._tooltip_win:
            try:
                self._tooltip_win.destroy()
            except Exception:
                pass
            self._tooltip_win = None

    def _stop_thumb_scroll(self):
        if hasattr(self, '_thumb_batch_id') and self._thumb_batch_id:
            try:
                self.root.after_cancel(self._thumb_batch_id)
            except Exception:
                pass
            self._thumb_batch_id = None

    def populate_thumbnail_grid(self):
        for widget in self.grid_scroll_frame.winfo_children():
            try:
                widget.destroy()
            except Exception:
                pass

        if not self.image_paths:
            return

        self._stop_thumb_scroll()
        self._thumbnail_tk_images = []
        self._thumb_rendered = 0
        self._thumb_batch_id = None
        self._thumb_cells = {}
        self._thumb_render_next_batch()

    def _thumb_render_next_batch(self):
        BATCH = 20
        cols = 3
        start = self._thumb_rendered
        end = min(self._thumb_rendered + BATCH, len(self.image_paths))

        for idx in range(start, end):
            cell = self._build_thumb_cell(idx, cols)
            if cell:
                self._thumb_cells[idx] = cell

        self._thumb_rendered = end

        if self._thumb_rendered < len(self.image_paths):
            self._thumb_batch_id = self.root.after(50, self._thumb_render_next_batch)

    def _thumb_colors(self, selected):
        if ctk.get_appearance_mode().lower() == "light":
            return ("#f3f4f6", "#f5c518" if selected else "#e5e7eb")
        return ("#1a1a1a", "#f5c518" if selected else "#1a1a1a")

    def _build_thumb_cell(self, idx, cols):
        path = self.image_paths[idx]
        try:
            raw_img = Image.open(path)
            flat_img = Image.new("RGB", (70, 52), (26, 26, 26))
            temp_img = raw_img.copy()
            temp_img.thumbnail((70, 52), Image.Resampling.NEAREST)
            x_offset = (70 - temp_img.width) // 2
            y_offset = (52 - temp_img.height) // 2
            if raw_img.mode in ("RGBA", "LA") or (raw_img.mode == "P" and "transparency" in raw_img.info):
                try:
                    alpha_mask = temp_img.convert("RGBA").split()[3]
                    flat_img.paste(temp_img, (x_offset, y_offset), mask=alpha_mask)
                except Exception:
                    flat_img.paste(temp_img.convert("RGB"), (x_offset, y_offset))
            else:
                flat_img.paste(temp_img.convert("RGB"), (x_offset, y_offset))

            tk_thumb = ImageTk.PhotoImage(flat_img)
            self._thumbnail_tk_images.append(tk_thumb)

            is_selected = (idx == self.still_index)
            bg_clr, border_clr = self._thumb_colors(is_selected)
            text_clr = "#4b5563" if ctk.get_appearance_mode().lower() == "light" else self.sub_color_adaptive[1]

            filename = os.path.basename(path)
            display_name = filename[:9] + "..." if len(filename) > 12 else filename

            cell = tk.Frame(self.grid_scroll_frame, bg=bg_clr,
                            highlightbackground=border_clr,
                            highlightthickness=3,
                            bd=0, width=92, height=105)
            cell.pack_propagate(False)

            img_lbl = tk.Label(cell, image=tk_thumb, bg=bg_clr, bd=0, cursor="hand2")
            img_lbl.pack(pady=(4, 2), padx=4)

            txt_lbl = tk.Label(cell, text=f"Frame {idx + 1}\n{display_name}",
                               font=(self.font_family, 9), fg=text_clr, bg=bg_clr,
                               justify="center", cursor="hand2")
            txt_lbl.pack(pady=(0, 4), padx=4, fill="x")

            for widget in (cell, img_lbl, txt_lbl):
                widget.bind("<Button-1>", lambda e, c=cell: self._on_thumb_click(c))
            for widget in (img_lbl, txt_lbl):
                widget.bind("<Enter>", lambda e, c=cell: self._on_thumb_hover(e, c))
                widget.bind("<Leave>", self.hide_grid_tooltip)

            row = idx // cols
            col = idx % cols
            cell.grid(row=row, column=col, padx=8, pady=8)
            return cell
        except Exception as e:
            print(f"Thumb cell {idx} error: {e}")
            return None

    def _update_thumb_selection(self, old_idx, new_idx):
        def_bg, def_border = self._thumb_colors(False)
        sel_bg, sel_border = self._thumb_colors(True)

        for idx, bg, border, thick in [
            (old_idx, def_bg, def_border, 3),
            (new_idx, sel_bg, sel_border, 3),
        ]:
            cell = self._thumb_cells.get(idx)
            if not cell:
                continue
            cell.configure(bg=bg, highlightbackground=border, highlightthickness=thick)
            for child in cell.winfo_children():
                try:
                    child.configure(bg=bg)
                except Exception:
                    pass
        self._apply_grab_tint()

    def _apply_grab_tint(self):
        """The grabbed frame (Enter during rearrange) gets a tinted tile so the grab is
        visible: the gold outline alone is the plain cursor, so without this Enter looks
        like it did nothing."""
        cell = self._thumb_cells.get(self.still_index)
        if not cell:
            return
        if self._rearrange_mode and self._rearrange_grab:
            bg = ("#fde68a" if ctk.get_appearance_mode().lower() == "light" else "#6b5510")
        else:
            bg = self._thumb_colors(True)[0]
        try:
            cell.configure(bg=bg)
            for child in cell.winfo_children():
                child.configure(bg=bg)
        except Exception:
            pass

    def _thumb_index_of(self, cell):
        for idx, c in self._thumb_cells.items():
            if c is cell:
                return idx
        return None

    def _on_thumb_click(self, cell):
        idx = self._thumb_index_of(cell)
        if idx is not None:
            self.single_click_grid_image(idx)

    def _on_thumb_hover(self, event, cell):
        idx = self._thumb_index_of(cell)
        if idx is not None:
            self.show_grid_tooltip(event, os.path.basename(self.image_paths[idx]))

    def _swap_thumb_cells(self, idx1, idx2):
        if idx1 not in self._thumb_cells or idx2 not in self._thumb_cells:
            return
        cell1 = self._thumb_cells[idx1]
        cell2 = self._thumb_cells[idx2]
        info1, info2 = cell1.grid_info(), cell2.grid_info()
        cell1.grid_remove()
        cell2.grid_remove()
        cell1.grid(row=info2['row'], column=info2['column'], padx=8, pady=8)
        cell2.grid(row=info1['row'], column=info1['column'], padx=8, pady=8)
        self._thumb_cells[idx1], self._thumb_cells[idx2] = cell2, cell1

    def delete_selected_frame(self):
        if not self.image_paths or len(self.image_paths) <= 1:
            return
        self.play_sound('del.mp3')
        del self.image_paths[self.still_index]

        self.still_index = min(self.still_index, len(self.image_paths) - 1)
        self.video_path = self.image_paths[self.still_index]
        if len(self.image_paths) == 1:
            self.file_name_label.configure(text=os.path.basename(self.video_path))
            self.export_btn.configure(text="Export Frame")
        else:
            self.file_name_label.configure(text=f"{len(self.image_paths)} Images Loaded")
            self.export_btn.configure(text="Export Frames")

        if self.current_singular_view == "grid":
            self.populate_thumbnail_grid()
        else:
            self.update_frame_display()
        self.update_nav_buttons_state()
        self.check_timing_warnings(show_popup=False)

    def move_frame_left(self):
        if self.still_index > 0:
            self.play_sound('moveleft.mp3')
            idx = self.still_index
            self.image_paths[idx], self.image_paths[idx - 1] = self.image_paths[idx - 1], self.image_paths[idx]
            self.still_index -= 1
            self.video_path = self.image_paths[self.still_index]
            self._swap_thumb_cells(idx, idx - 1)
            self._update_thumb_selection(idx, self.still_index)
            self.update_nav_buttons_state()

    def move_frame_right(self):
        if self.still_index < len(self.image_paths) - 1:
            self.play_sound('moveright.mp3')
            idx = self.still_index
            self.image_paths[idx], self.image_paths[idx + 1] = self.image_paths[idx + 1], self.image_paths[idx]
            self.still_index += 1
            self.video_path = self.image_paths[self.still_index]
            self._swap_thumb_cells(idx, idx + 1)
            self._update_thumb_selection(idx, self.still_index)
            self.update_nav_buttons_state()

    def load_user_settings(self):
        src = self.config_filepath
        if not os.path.exists(src) and os.path.exists(self._legacy_config_filepath):
            src = self._legacy_config_filepath
        if IS_LINUX and not os.path.exists(src):
            old_linux_path = os.path.join(
                os.path.expanduser("~"), ".config", "sigmaflip", ".sigmaflip_config.json")
            if os.path.exists(old_linux_path):
                src = old_linux_path
        if not os.path.exists(src):
            return
        try:
            with open(src, "r") as f:
                config = json.load(f)

            advanced = config.get("advanced_settings", {})
            for k in self.advanced_settings:
                if k in advanced:
                    self.advanced_settings[k] = advanced[k]
            self.advanced_settings["pixel_precision"] = bool(self.advanced_settings.get("pixel_precision", False))
            self.advanced_settings["black_and_white"] = bool(self.advanced_settings.get("black_and_white", False))
            self.advanced_settings["contrast"] = max(0.1, min(3.0, float(self.advanced_settings.get("contrast", 1.0))))
            self.advanced_settings["album_capacity"] = max(1, int(self.advanced_settings.get("album_capacity", 100)))
            self.advanced_settings["jpg_quality"] = max(1, min(100, int(self.advanced_settings.get("jpg_quality", 95))))
            self.advanced_settings["watermark_scale"] = max(5, min(100, int(self.advanced_settings.get("watermark_scale", 40))))
            wm_path = self.advanced_settings.get("watermark_path")
            if wm_path and not os.path.isfile(wm_path):
                self.advanced_settings["watermark_path"] = ""
            if self.advanced_settings.get("dither_mode") in ("Bayer 16x16", "Bayer 32x32"):
                self.advanced_settings["dither_mode"] = "None"

            self.audio_enabled = config.get("audio_enabled", True)
            self.continuous_export = config.get("continuous_export", False)
            self.bg_type = config.get("bg_type", "black")
            self.bg_image_path = config.get("bg_image_path", None)
            self.export_structure = config.get("export_structure", "dcim")
            self.console_type = config.get("console_type", "dsi")
            self.bg_type_var.set(self.bg_type)
            self.export_structure_var.set(self.export_structure)
            self.audio_menu_var.set(self.audio_enabled)
            self.continuous_export_var.set(self.continuous_export)
            self.set_bg_menu_type(self.bg_type, silent=True)
            self.set_struct_menu_type(self.export_structure, silent=True)
            self.set_console_type(self.console_type)

            scale_modes_map = {
                "Fit": "Fit (Letterbox)",
                "Stretch": "Stretch",
                "Crop": "Crop (4:3)",
                "Tiles": "Tiles",
                "Tiles Stretched": "Tiles Stretched"
            }
            raw_scale = config.get("scale_mode", "Fit")
            self.scale_mode = raw_scale if raw_scale in scale_modes_map else "Fit"
            self.aspect_menu.set(scale_modes_map.get(self.scale_mode, "Fit (Letterbox)"))

            stored_mode = config.get("export_mode", "Video Frames")
            if stored_mode == "Singular Image":
                stored_mode = "Still Images"
            self.export_mode_var.set(stored_mode)
            self.export_mode_menu.set(stored_mode)

            if stored_mode == "Still Images":
                self.repack_singular_image_layout()
                self.update_nav_buttons_state()
            else:
                self.repack_video_layout()

            if src == self._legacy_config_filepath:
                try:
                    os.remove(src)
                except OSError:
                    pass

        except Exception as e:
            print(f"Error restoring user configurations: {e}")

    def save_user_settings(self):
        try:
            config_data = {
                "export_mode": self.export_mode_var.get(),
                "continuous_export": self.continuous_export,
                "audio_enabled": self.audio_enabled,
                "bg_type": self.bg_type,
                "bg_image_path": self.bg_image_path if self.bg_image_path else "",
                "export_structure": self.export_structure,
                "console_type": self.console_type,
                "scale_mode": self.scale_mode,
                "advanced_settings": self.advanced_settings
            }
            with open(self.config_filepath, "w") as f:
                json.dump(config_data, f, indent=4)
            if not IS_WINDOWS:
                os.chmod(self.config_filepath, 0o600)
        except Exception as e:
            print(f"Error saving user configurations: {e}")

    def _forget_all_widgets(self):
        for w in (
            self.timeline_slider, self.limit_indicator, self.tile_config_row,
            self.sf_frame, self.config_row, self.ctrl_row, self.progress_bar,
            self.nav_row, self.grid_controls_row, self.toggle_view_btn,
            self.grid_scroll_frame, self.load_btn, self.play_btn, self.export_btn,
        ):
            w.pack_forget()

    def repack_video_layout(self):
        self._forget_all_widgets()

        self.video_canvas.pack(fill="both", expand=True)
        self.current_singular_view = "preview"
        self.toggle_view_btn.configure(text="Switch to Grid View")
        self.export_btn.configure(text="Export Frames")

        self.timeline_slider.pack(fill="x", padx=40, pady=5)
        self.limit_indicator.pack(pady=3)
        if self.scale_mode in ("Tiles", "Tiles Stretched"):
            self.tile_config_row.pack(pady=4)
        self.sf_frame.pack(pady=4)
        self.config_row.pack(pady=4, fill="x", padx=45)

        self.load_btn.pack(side=tk.LEFT, padx=3)
        self.play_btn.pack(side=tk.LEFT, padx=3)
        self.export_btn.pack(side=tk.LEFT, padx=3)

        self.ctrl_row.pack(pady=5)
        self.progress_bar.pack(fill="x", padx=45, pady=8)

    def repack_singular_image_layout(self):
        self._forget_all_widgets()

        self.toggle_view_btn.pack(pady=4)
        if self.current_singular_view == "grid":
            self.grid_scroll_frame.pack(fill="both", expand=True)
            self.grid_controls_row.pack(pady=5)
            self.nav_row.pack(pady=5)
        else:
            self.video_canvas.pack(fill="both", expand=True)
            self.nav_row.pack(pady=5)

        self.limit_indicator.pack(pady=3)
        if self.scale_mode in ("Tiles", "Tiles Stretched"):
            self.tile_config_row.pack(pady=4)
        self.config_row.pack(pady=4, fill="x", padx=45)

        self.load_btn.pack(side=tk.LEFT, padx=3)
        self.export_btn.pack(side=tk.LEFT, padx=3)

        self.ctrl_row.pack(pady=5)
        self.progress_bar.pack(fill="x", padx=45, pady=8)

    def on_export_mode_change(self, value):
        self.play_sound('apply.mp3')

        if self.cap:
            self.cap.release()
            self.cap = None
        if self.gif_img:
            self.gif_img.close()
            self.gif_img = None
        self.video_path = None
        self.image_paths = []
        self.file_name_label.configure(text="No File Loaded")
        self._set_icon(self.play_btn, 'play', "disabled")
        self.export_btn.configure(state="disabled")
        self.current_frame_idx = 0.0
        self.timeline_slider.set(0)
        self.video_canvas.delete("all")

        self._cleanup_audio()

        if value == "Still Images":
            self.current_singular_view = "preview"
            self.toggle_view_btn.configure(text="Switch to Grid View")
            self.repack_singular_image_layout()
            self.file_name_label.configure(text="No Image Loaded")
            self.export_btn.configure(text="Export Frame")
            self.limit_indicator.configure(text="Export Frames: 0 / 999", text_color=self.sub_color_adaptive)
            self.update_nav_buttons_state()
        else:
            self.repack_video_layout()
            self._set_icon(self.play_btn, 'play', "disabled")
            self.file_name_label.configure(text="No Video Loaded")
            self.limit_indicator.configure(
                text="Export Frames: 0 / 999",
                text_color=self.sub_color_adaptive
            )
            self.export_btn.configure(text="Export Frames")

    def toggle_export_mode(self):
        new_mode = "Still Images" if self.export_mode_var.get() != "Still Images" else "Video Frames"
        self.export_mode_var.set(new_mode)
        self.on_export_mode_change(new_mode)

    def load_video_dialog(self):
        if self.playing:
            self.toggle_play()

        self.play_sound('upload.mp3')

        if self.export_mode_var.get() == "Still Images":
            file_paths = filedialog.askopenfilenames(
                filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.webp")]
            )
            if not file_paths:
                self.play_sound('back.mp3')
                return

            valid_paths = []
            invalid_files = []
            for selected_path in file_paths:
                valid, error = self._validate_still_image(selected_path)
                if valid:
                    valid_paths.append(selected_path)
                else:
                    invalid_files.append(f"{os.path.basename(selected_path)}: {error}")

            if not valid_paths:
                self.play_sound('warning.mp3')
                messagebox.showerror(
                    "No Valid Images",
                    "None of the selected images could be safely loaded.\n\n"
                    + "\n".join(invalid_files[:8])
                )
                return

            if invalid_files:
                self.play_sound('warning.mp3')
                messagebox.showwarning(
                    "Some Images Skipped",
                    "The following files were skipped because they could not be safely loaded:\n\n"
                    + "\n".join(invalid_files[:8])
                    + ("\n\nAdditional invalid files were also skipped." if len(invalid_files) > 8 else "")
                )

            self.image_paths = valid_paths
            self.still_index = 0
            self.video_path = self.image_paths[self.still_index]

            if len(self.image_paths) == 1:
                self.file_name_label.configure(text=os.path.basename(self.video_path))
                self.export_btn.configure(text="Export Frame")
            else:
                self.file_name_label.configure(text=f"{len(self.image_paths)} Images Loaded")
                self.export_btn.configure(text="Export Frames")

            self.export_btn.configure(state="normal")
            self.update_nav_buttons_state()

            if self.current_singular_view == "grid":
                self.populate_thumbnail_grid()
            else:
                self.update_frame_display()

            self.check_timing_warnings(show_popup=False)
            return

        file_path = filedialog.askopenfilename(
            filetypes=[("Video files", "*.mp4 *.avi *.mov *.mkv *.gif")]
        )
        if not file_path:
            self.play_sound('back.mp3')
            return

        if not file_path.lower().endswith(VIDEO_EXTS):
            self.play_sound('warning.mp3')
            messagebox.showerror(
                "Unsupported File",
                "Video Frames mode accepts only videos and GIFs (MP4, AVI, MOV, MKV, GIF).\n\n"
                "To load a still image, choose \"Still Images\" from the Export Mode menu, then load the file again."
            )
            return

        if self.cap:
            self.cap.release()
        if self.gif_img:
            self.gif_img.close()
            self.gif_img = None

        self._cleanup_audio()

        self.video_path = file_path
        self.image_paths = []
        self.file_name_label.configure(text=os.path.basename(file_path))
        self.export_btn.configure(state="normal")

        self.cap = FFmpegVideoCapture(file_path)
        if not self.cap.isOpened():
            self.play_sound('warning.mp3')
            messagebox.showerror(
                "Unsupported Video",
                "This video could not be decoded.\n\n"
                "SIGMAFLIP reads frames through FFmpeg/ffprobe; make sure both are installed "
                "and available on your PATH."
            )
            self.cap = None
            self.video_path = None
            self.file_name_label.configure(text="No File Loaded")
            self.export_btn.configure(state="disabled")
            return
        self.total_video_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.video_fps = self.cap.get(cv2.CAP_PROP_FPS)
        if self.video_fps <= 0:
            self.video_fps = 24.0
        if file_path.lower().endswith('.gif') and self.total_video_frames > 1:
            try:
                self.gif_img = Image.open(file_path)
                total_ms = 0
                for i in range(getattr(self.gif_img, 'n_frames', self.total_video_frames)):
                    self.gif_img.seek(i)
                    total_ms += self.gif_img.info.get('duration', 100)
                self.gif_img.seek(0)
                if total_ms > 0:
                    self.video_fps = self.total_video_frames / (total_ms / 1000.0)
            except Exception:
                if self.gif_img:
                    self.gif_img.close()
                    self.gif_img = None
        while self.total_video_frames > 1:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.total_video_frames - 1)
            ret, _ = self.cap.read()
            if ret:
                break
            self.total_video_frames -= 1
        if not file_path.lower().endswith('.gif'):
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg:
                try:
                    r = subprocess.run([ffmpeg, "-i", os.path.abspath(file_path)], capture_output=True, text=True, creationflags=_NO_WINDOW)
                    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr or "")
                    if m:
                        h, mi, s = m.groups()
                        real_dur = int(h) * 3600 + int(mi) * 60 + float(s)
                        if real_dur > 0:
                            self.video_fps = self.total_video_frames / real_dur
                except Exception:
                    pass
        self.video_duration = self.total_video_frames / self.video_fps

        self._set_icon(self.play_btn, 'play', "normal")
        self.current_frame_idx = 0.0
        self.timeline_slider.configure(from_=0, to=max(1, self.total_video_frames - 1))
        self.timeline_slider.set(0)

        if self.audio_enabled:
            threading.Thread(target=self.extract_audio_thread, daemon=True).start()

        self.sync_speed_widget_image()
        self.check_timing_warnings(show_popup=True)
        self.update_frame_display()

    def _navigate(self, new_idx, sound):
        if not self.image_paths:
            return
        old_idx = self.still_index
        if new_idx == old_idx:
            return
        self.still_index = new_idx
        self.play_sound(sound)
        self.video_path = self.image_paths[self.still_index]
        self.update_nav_buttons_state()
        if self.current_singular_view == "grid":
            self._update_thumb_selection(old_idx, self.still_index)
        else:
            self.update_frame_display()

    def show_prev_image(self):
        if self.still_index > 0:
            self._navigate(self.still_index - 1, 'prev.mp3')

    def show_next_image(self):
        if self.still_index < len(self.image_paths) - 1:
            self._navigate(self.still_index + 1, 'next.mp3')

    def jump_to_beginning(self):
        if self.image_paths and self.still_index != 0:
            self._navigate(0, 'beg.mp3')

    def jump_to_end(self):
        if not self.image_paths:
            return
        last = len(self.image_paths) - 1
        if self.still_index != last:
            self._navigate(last, 'end.mp3')

    def update_nav_buttons_state(self):
        total = len(self.image_paths)

        if hasattr(self, "delete_frame_btn"):
            if total <= 1:
                self.delete_frame_btn.configure(state="disabled")
            else:
                self.delete_frame_btn.configure(state="normal")
        if hasattr(self, "move_left_btn"):
            last = total - 1
            self.move_left_btn.configure(state="disabled" if self.still_index <= 0 else "normal")
            self.move_right_btn.configure(state="disabled" if self.still_index >= last else "normal")

        if total <= 1:
            self._set_icon(self.beg_btn, 'beg', "disabled")
            self._set_icon(self.prev_btn, 'prev', "disabled")
            self._set_icon(self.next_btn, 'next', "disabled")
            self._set_icon(self.end_btn, 'end', "disabled")
            self.nav_label.configure(text=f"1 of {total}" if total == 1 else "0 of 0")
            return

        self.nav_label.configure(text=f"{self.still_index + 1} of {total}")

        if self.still_index == 0:
            self._set_icon(self.beg_btn, 'beg', "disabled")
        else:
            self._set_icon(self.beg_btn, 'beg', "normal")

        if self.still_index == 0:
            self._set_icon(self.prev_btn, 'prev', "disabled")
        else:
            self._set_icon(self.prev_btn, 'prev', "normal")

        if self.still_index == total - 1:
            self._set_icon(self.next_btn, 'next', "disabled")
        else:
            self._set_icon(self.next_btn, 'next', "normal")

        if self.still_index == total - 1:
            self._set_icon(self.end_btn, 'end', "disabled")
        else:
            self._set_icon(self.end_btn, 'end', "normal")

    def extract_audio_thread(self):
        """Asynchronously extracts audio from video in an easily seekable format (OGG Vorbis -> MP3 -> WAV)."""
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin or not self.video_path:
            return

        self._temp_audio_dir = tempfile.mkdtemp(prefix="sigmaflip_preview_")
        base_tmp = os.path.join(self._temp_audio_dir, "audio")

        candidates = [
            (f"{base_tmp}.ogg", ["-c:a", "libvorbis", "-q:a", "4"]),
            (f"{base_tmp}.ogg", ["-c:a", "vorbis", "-q:a", "4"]),
            (f"{base_tmp}.mp3", ["-c:a", "libmp3lame", "-q:a", "3"]),
            (f"{base_tmp}.mp3", ["-q:a", "3"]),
            (f"{base_tmp}.wav", ["-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2"])
        ]

        extracted_path = None
        for path_candidate, codec_args in candidates:
            cmd = [ffmpeg_bin, "-y", "-i", os.path.abspath(self.video_path), "-vn"] + codec_args + [path_candidate]
            try:
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
                if res.returncode == 0 and os.path.exists(path_candidate) and os.path.getsize(path_candidate) > 100:
                    extracted_path = path_candidate
                    break
            except Exception:
                continue

        if extracted_path:
            self.temp_audio_path = extracted_path
            self._ui_call(self.load_extracted_audio)
        else:
            shutil.rmtree(self._temp_audio_dir, ignore_errors=True)
            self._temp_audio_dir = None

    def load_extracted_audio(self):
        try:
            pygame.mixer.music.load(self.temp_audio_path)
            pygame.mixer.music.set_volume(0.7)
            self.has_audio = True
            try:
                snd = pygame.mixer.Sound(self.temp_audio_path)
                self.audio_duration = snd.get_length()
            except Exception:
                self.audio_duration = self.video_duration
            if self.playing:
                self.start_audio_at_current_frame()
        except Exception:
            self.has_audio = False
            self.audio_duration = 0.0

    def get_effective_duration(self):
        duration = self.video_duration
        if self.has_audio and getattr(self, 'audio_duration', 0.0) > 0:
            duration = max(duration, self.audio_duration)
        elif self.temp_audio_path and os.path.exists(self.temp_audio_path):
            try:
                snd = pygame.mixer.Sound(self.temp_audio_path)
                duration = max(duration, snd.get_length())
            except Exception:
                pass
        return duration

    def get_exact_export_frame_count(self) -> int:
        target_fps = SPEED_FPS[self.speed]
        dur = self.get_effective_duration()
        return max(1, math.ceil(dur * target_fps))

    def check_timing_warnings(self, show_popup=False):
        if self.export_mode_var.get() == "Still Images":
            self.update_indicator_metrics(0)
            return

        target_fps = SPEED_FPS[self.speed]
        estimated_frames = self.get_exact_export_frame_count()

        if show_popup and self.video_duration > WARNING_DURATION:
            self.play_sound('warning.mp3')
            messagebox.showwarning(
                "SIGMAFLIP Warning",
                f"Your selected video is {self.video_duration:.1f} seconds long, which exceeds the recommended 60-second limit.\n\n"
                "Flipnote Studio limits files to 999 frames and enforces strict per-frame memory caps "
                "(derived from ink usage, line counts, and color complexity).\n\n"
                f"At the chosen Speed {self.speed} ({target_fps} FPS), this will yield exactly {estimated_frames} frames.\n\n"
                "To avoid app crashes or failed imports, please consider trimming this video "
                "or opting for a lower playback speed setting."
            )
        self.update_indicator_metrics(estimated_frames)

    def update_indicator_metrics(self, estimated_frames):
        if self.export_mode_var.get() == "Still Images":
            count = len(self.image_paths)
        else:
            count = estimated_frames
        self.limit_indicator.configure(
            text=f"Export Frames: {count} / {MAX_FRAMES}",
            text_color=("#b91c1c", "#FF4D4D") if count > MAX_FRAMES else self.main_color_adaptive
        )

    def set_scale_mode(self, value):
        self.play_sound('apply.mp3')
        if value == "Fit (Letterbox)":
            self.scale_mode = "Fit"
            self.tile_config_row.pack_forget()
        elif value == "Stretch":
            self.scale_mode = "Stretch"
            self.tile_config_row.pack_forget()
        elif value == "Crop (4:3)":
            self.scale_mode = "Crop"
            self.tile_config_row.pack_forget()
        elif value in ("Tiles", "Tiles Stretched"):
            self.scale_mode = value
            if self.export_mode_var.get() == "Still Images":
                self.repack_singular_image_layout()
            else:
                self.repack_video_layout()

        if self.cap or self.image_paths:
            self.check_timing_warnings(show_popup=False)
        self.update_frame_display()

    def update_frame_display(self):
        if not self.video_path:
            return

        is_singular = (self.export_mode_var.get() == "Still Images")

        canvas_w = self.video_canvas.winfo_width()
        canvas_h = self.video_canvas.winfo_height()
        if canvas_w < 10 or canvas_h < 10:
            canvas_w, canvas_h = 320, 240

        if is_singular:
            try:
                pil_img = Image.open(self.video_path).convert("RGBA")
                pil_img = self.apply_scaling_to_image(pil_img, canvas_w, canvas_h)
                self.tk_image = ImageTk.PhotoImage(pil_img)
                self.video_canvas.delete("all")
                self.video_canvas.create_image(canvas_w // 2, canvas_h // 2, image=self.tk_image, anchor="center")
            except Exception as e:
                print(f"Still image loading preview error: {e}")
            return

        if self.gif_img:
            try:
                self.gif_img.seek(int(self.current_frame_idx))
                pil_img = self.gif_img.convert("RGBA")
            except Exception:
                return
        elif self.cap:
            target_frame = int(self.current_frame_idx)
            current_pos = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))

            if target_frame != current_pos:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                current_pos = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))

            if target_frame < current_pos:
                return

            ret, frame = self.cap.read()
            if not ret:
                return
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(frame)
        else:
            return

        pil_img = self.apply_scaling_to_image(pil_img, canvas_w, canvas_h)

        self.tk_image = ImageTk.PhotoImage(pil_img)
        self.video_canvas.delete("all")
        self.video_canvas.create_image(canvas_w // 2, canvas_h // 2, image=self.tk_image, anchor="center")

    def request_preview_refresh(self):
        if self.playing:
            return
        if self._preview_refresh_id is None:
            self._preview_refresh_id = self.root.after(16, self.flush_preview_refresh)

    def flush_preview_refresh(self):
        self._preview_refresh_id = None
        self.update_frame_display()

    def apply_scaling_to_image(self, img, target_w, target_h):
        return _apply_scaling_filter_mod(
            img, target_w, target_h,
            self.bg_type, self.scale_mode, self.get_grid_dimensions(),
            self.advanced_settings, self.bg_image_path,
            self._exporting, self.playing,
            os.path.join(self.img_path, "default_watermark.png"),
            self.export_mode_var.get() != "Still Images")

    def on_slider_press(self, event=None):
        if not self.cap and not self.gif_img:
            return
        self._is_scrubbing = True
        self._was_playing_before_scrub = self.playing
        if self.playing:
            try:
                pygame.mixer.music.pause()
            except Exception:
                pass

    def on_slider_release(self, event=None):
        if not self._is_scrubbing:
            return
        self._is_scrubbing = False

        if self._was_playing_before_scrub:
            self._was_playing_before_scrub = False
            self.playing = True
            self._playback_start_time = time.perf_counter()
            self._playback_start_frame = self.current_frame_idx
            self.start_audio_at_current_frame()
            if not self.after_play_id:
                self.playback_tick()
        else:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass

    def on_slider_scrub(self, val):
        if not self.cap and not self.gif_img:
            return
        frame_idx = float(val)
        target_fps = SPEED_FPS[self.speed]
        frame_step = max(1, round(self.video_fps / target_fps))
        if frame_step > 1:
            frame_idx = int(frame_idx // frame_step) * frame_step
        self.current_frame_idx = frame_idx
        self.timeline_slider.set(int(frame_idx))

        self.update_frame_display()

        if not self._is_scrubbing and self.playing:
            self._playback_start_time = time.perf_counter()
            self._playback_start_frame = self.current_frame_idx
            self.start_audio_at_current_frame()

    def start_audio_at_current_frame(self):
        if not (self.audio_enabled and self.has_audio):
            return
        if self.video_fps <= 0:
            return
        start_sec = max(0.0, self.current_frame_idx / self.video_fps)
        if hasattr(self, 'audio_duration') and self.audio_duration > 0 and start_sec >= self.audio_duration:
            return
        try:
            pygame.mixer.music.play(start=start_sec)
        except Exception as e:
            print(f"[SIGMAFLIP] Audio preview playback notice: {e}")

    def toggle_play(self):
        if not self.cap or (self.export_mode_var.get() == "Still Images"):
            return

        if self.playing:
            self.playing = False
            self._set_icon(self.play_btn, 'play')
            self.play_sound('stoppause.mp3')

            try:
                pygame.mixer.music.stop()
            except Exception:
                pass

            if self.after_play_id:
                self.root.after_cancel(self.after_play_id)
                self.after_play_id = None
            self.sync_speed_widget_image()
            if self.advanced_settings.get("performance_mode", False):
                self.update_frame_display()
        else:
            self.playing = True
            self._set_icon(self.play_btn, 'pause')
            self.play_sound('playresume.mp3')
            self.sync_speed_widget_image()

            self._playback_start_time = time.perf_counter()
            self._playback_start_frame = self.current_frame_idx

            self.start_audio_at_current_frame()
            self.playback_tick()

    def playback_tick(self):
        if not self.playing:
            return

        if self._is_scrubbing:
            target_fps = SPEED_FPS[self.speed]
            interval_ms = max(10, int(1000.0 / target_fps))
            self.after_play_id = self.root.after(interval_ms, self.playback_tick)
            return

        target_fps = SPEED_FPS[self.speed]
        frame_step = max(1, round(self.video_fps / target_fps))
        elapsed = time.perf_counter() - self._playback_start_time

        self.current_frame_idx = self._playback_start_frame + (elapsed * self.video_fps)

        if frame_step > 1:
            self.current_frame_idx = int(self.current_frame_idx // frame_step) * frame_step

        if self.current_frame_idx >= self.total_video_frames:
            self.current_frame_idx = 0.0
            self._playback_start_time = time.perf_counter()
            self._playback_start_frame = 0.0
            self.start_audio_at_current_frame()

        self.timeline_slider.set(int(self.current_frame_idx))
        self.update_frame_display()

        interval = 1.0 / target_fps
        next_at = interval * (math.floor(elapsed / interval) + 1)
        delay_ms = max(1, int((next_at - elapsed) * 1000))
        self.after_play_id = self.root.after(delay_ms, self.playback_tick)

    def _seek_seconds(self, delta):
        if not (self.cap or self.gif_img) or self._is_scrubbing:
            return
        sec = max(0.0, self.current_frame_idx / self.video_fps + delta)
        max_sec = max(0.0, (self.total_video_frames - 1) / self.video_fps)
        sec = min(sec, max_sec)
        self.on_slider_scrub(int(sec * self.video_fps))

    def _jump_video_fraction(self, digit):
        if not (self.cap or self.gif_img) or self._is_scrubbing:
            return
        self.on_slider_scrub(int((self.total_video_frames - 1) * (digit / 9.0)))

    def stop_playback(self):
        if self.export_mode_var.get() == "Still Images":
            return
        if self.playing:
            self.toggle_play()
        if self.cap or self.gif_img:
            self.current_frame_idx = 0
            self.timeline_slider.set(0)
            self.update_frame_display()

    def _throttle(self, key, gap=0.3):
        now = time.perf_counter()
        last = getattr(self, "_last_key_times", {})
        if now - last.get(key, 0.0) < gap:
            return True
        last[key] = now
        self._last_key_times = last
        return False

    def on_global_key(self, event):
        if getattr(self, "_exporting", False):
            return None
        try:
            if str(event.widget.winfo_toplevel()) != str(self.root):
                return None
        except Exception:
            pass
        if event.widget.winfo_class() in ("Entry", "Spinbox", "Text"):
            return None
        ks = event.keysym
        if not ks:
            return None
        mods = event.state
        ctrl = bool(mods & 0x4)
        shift = bool(mods & 0x1)
        is_singular = (self.export_mode_var.get() == "Still Images")
        have_video = bool(self.cap or self.gif_img)
        ksl = ks.lower()

        if ctrl or (IS_MAC and bool(mods & 0x8)):
            if ksl == "o":
                if shift:
                    self.toggle_export_mode()
                else:
                    self.load_video_dialog()
                return "break"
            if ksl == "s":
                self.export_frames()
                return "break"
            return None

        if ks in ("Up", "Down"):
            if is_singular or self.playing:
                return "break"
            delta = 1 if ks == "Up" else -1
            self.set_flipnote_speed(max(1, min(8, self.speed + delta)))
            return "break"

        if ks == "backslash":
            if self.scale_mode in ("Tiles", "Tiles Stretched"):
                if self._throttle("backslash"):
                    return "break"
                self.toggle_tile_link()
                return "break"
            return None

        if ks == "space":
            if self._throttle("space"):
                return "break"
            self.toggle_play()
            return "break"

        if ks == "Escape":
            if self._rearrange_mode:
                self.exit_rearrange_mode()
            elif not is_singular:
                self.stop_playback()
            return "break"

        if is_singular:
            if ks in ("grave", "asciitilde", "`", "~"):
                if self._rearrange_mode:
                    self.play_sound('apply.mp3')
                    self.exit_rearrange_mode()
                    if self.current_singular_view == "grid":
                        self.switch_to_preview_view()
                elif self.image_paths:
                    self.play_sound('apply.mp3')
                    self.start_rearrange_mode()
                return "break"
            if ks == "BackSpace":
                if self._throttle("delete_frame"):
                    return "break"
                self.delete_selected_frame()
                return "break"
            if self._rearrange_mode:
                if ks == "Return":
                    self.play_sound('apply.mp3')
                    self._rearrange_grab = not self._rearrange_grab
                    self._update_thumb_selection(None, self.still_index)
                    return "break"
                if ks == "Left":
                    if self._rearrange_grab:
                        self.move_frame_left()
                    else:
                        self.show_prev_image()
                    return "break"
                if ks == "Right":
                    if self._rearrange_grab:
                        self.move_frame_right()
                    else:
                        self.show_next_image()
                    return "break"
                return None
            if shift and ks == "Left":
                self.jump_to_beginning()
                return "break"
            if shift and ks == "Right":
                self.jump_to_end()
                return "break"
            if ks == "Left":
                self.show_prev_image()
                return "break"
            if ks == "Right":
                self.show_next_image()
                return "break"
            return None

        if not have_video:
            return None
        if ksl == "k":
            if self._throttle("space"):
                return "break"
            self.toggle_play()
            return "break"
        if ks == "Left":
            self._seek_seconds(-5)
            return "break"
        if ks == "Right":
            self._seek_seconds(5)
            return "break"
        if ksl == "j":
            self._seek_seconds(-10)
            return "break"
        if ksl == "l":
            self._seek_seconds(10)
            return "break"
        if ks in "0123456789":
            self._jump_video_fraction(int(ks))
            return "break"
        return None

    def show_keybinds(self):
        if getattr(self, "_keybind_win", None) is not None and self._keybind_win.winfo_exists():
            self._keybind_win.lift()
            self._keybind_win.focus_force()
            return

        win = ctk.CTkToplevel(self.root)
        self._keybind_win = win
        win.title("Keyboard Shortcuts")
        win.resizable(False, False)
        win.transient(self.root)
        win.configure(fg_color=("#f3f4f6", "#151515"))
        try:
            self._set_window_icon(win)
        except Exception:
            pass

        attach_grid_background(win)

        main = self.main_color_adaptive
        sub = self.sub_color_adaptive
        accent = self.highlight_color_adaptive

        pad_x, pad_y = 28, 16
        frame = ctk.CTkFrame(win, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=pad_x, pady=pad_y)

        GridLabel(
            frame, text="Keyboard Shortcuts", font=self.font_title,
            text_color=main, fg_color="transparent"
        ).pack(pady=(0, 10))

        key_font = symbol_font(self.font_family, 16, scaling=frame._get_widget_scaling())
        mod_name = "\u2318" if IS_MAC else "Ctrl"

        sections = [
            ("VIDEO PLAYBACK", [
                ("\u2191 / \u2193", "Change Flipnote speed"),
                ("Space / K", "Play / pause"),
                ("\u2190 / \u2192", "Skip backward / forward 5 seconds"),
                ("J / L", "Skip backward / forward 10 seconds"),
                ("0 - 9", "Jump to video position (0% - 90%)"),
                ("Escape", "Stop playback"),
            ]),
            ("STILL IMAGES MODE", [
                ("\u2190 / \u2192", "Previous / next image"),
                ("Shift+\u2190 / Shift+\u2192", "Jump to first / last image"),
                ("Backspace", "Delete the selected image"),
                ("`", "Rearrange images in grid view (Enter to select, \u2190/\u2192 to move)"),
            ]),
            ("OTHER", [
                (f"{mod_name}+O", "Load photos or video"),
                (f"{mod_name}+S", "Export frames"),
                (f"{mod_name}+Shift+O", "Switch between Video and Still Images mode"),
                ("\\", "Toggle tile linking"),
            ]),
        ]

        table = ctk.CTkFrame(frame, fg_color="transparent")
        table.pack(fill="both", expand=True)
        table.grid_columnconfigure(1, weight=1)

        row = 0
        for sec_title, items in sections:
            GridLabel(
                table, text=sec_title, font=self.font_small, anchor="n",
                text_color=accent, fg_color="transparent"
            ).grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 2))
            row += 1

            for key_str, desc_str in items:
                GridLabel(
                    table, text=key_str, font=key_font,
                    text_color=main, fg_color="transparent", width=190
                ).grid(row=row, column=0, sticky="w", padx=(6, 16), pady=1)

                GridLabel(
                    table, text=desc_str, font=self.font_tiny,
                    text_color=sub, fg_color="transparent"
                ).grid(row=row, column=1, sticky="w", pady=1)
                row += 1

        frame.update_idletasks()
        win.geometry(f"{frame.winfo_reqwidth() + 2 * pad_x}x"
                     f"{frame.winfo_reqheight() + 2 * pad_y}")

        def close():
            win.destroy()
            self._keybind_win = None

        win.protocol("WM_DELETE_WINDOW", close)

    def _notify_export_complete(self, title, message, directory=None):
        if directory:
            if messagebox.askyesno(title, message + "\n\nOpen the export folder?", parent=self.root):
                _open_in_file_manager(directory)
        else:
            messagebox.showinfo(title, message, parent=self.root)

    def start_rearrange_mode(self):
        if self.export_mode_var.get() != "Still Images" or not self.image_paths:
            return
        if self.current_singular_view != "grid":
            self.switch_to_grid_view()
        if self._rearrange_grab:
            return
        self._rearrange_mode = True
        self._update_thumb_selection(None, self.still_index)

    def exit_rearrange_mode(self):
        if not self._rearrange_mode:
            return
        self._rearrange_mode = False
        self._rearrange_grab = False
        self._update_thumb_selection(None, self.still_index)

    def get_export_worker_count(self) -> int:
        total_cores = os.cpu_count() or 1
        if total_cores <= 2:
            return 1
        if self.advanced_settings.get("performance_mode", False):
            return max(1, min(total_cores - 1, 2))
        return max(1, min(total_cores - 1, 6))

    def set_export_priority(self, low_end_mode: bool):
        if IS_WINDOWS:
            try:
                import ctypes
                handle = ctypes.windll.kernel32.GetCurrentProcess()
                priority = 0x00004000 if low_end_mode else 0x00000020
                ctypes.windll.kernel32.SetPriorityClass(handle, priority)
            except Exception:
                pass

    def _sign_and_partition(self, output_dir: str, sources: list[str], base_time: float, remove_sources: bool = False, progress_range: tuple = (0.0, 1.0), append_info: dict = None) -> tuple[int, int]:
        batch_size = max(1, int(self.advanced_settings.get("album_capacity", 100)))
        dsi_suffix = "NIN02" if self.console_type == "dsi" else "NIN01"
        use_parts = (self.export_structure == "parts")

        batch_index = 1
        folder_idx = 1 if use_parts else 100
        file_idx = 0
        used_in_batch = 0
        if append_info:
            batch_index = append_info["batch_index"]
            folder_idx = append_info["folder_idx"]
            file_idx = append_info["file_idx"]
            used_in_batch = append_info["used_in_batch"]
            base_time = append_info["next_base_time"]

        p_start, p_end = progress_range
        p_span = p_end - p_start

        jobs = []
        folder_sets = set()
        for src in sources:
            if file_idx >= 100:
                folder_idx += 1
                file_idx = 0
            if not use_parts and used_in_batch >= batch_size:
                batch_index += 1
                folder_idx = 100
                file_idx = 0
                used_in_batch = 0
            if use_parts:
                part_dir = os.path.join(output_dir, f"SFPart_{folder_idx}")
            else:
                batch_label = "DCIM" if batch_index == 1 else f"DCIM_{batch_index}"
                part_dir = os.path.join(output_dir, batch_label, f"{folder_idx}{dsi_suffix}")
            os.makedirs(part_dir, exist_ok=True)
            folder_sets.add(part_dir)

            file_idx += 1
            used_in_batch += 1
            frame_time = base_time + len(jobs) * 2
            time_str = time.strftime("%Y:%m:%d %H:%M:%S", time.localtime(frame_time))
            jobs.append([src, os.path.join(part_dir, f"HNI_{file_idx:04d}.JPG"), time_str, frame_time])

        if not jobs:
            return 0, len(folder_sets)
        total_jobs = len(jobs)

        params = {
            "bg_type": self.bg_type,
            "scale_mode": self.scale_mode,
            "grid_dims": self.get_grid_dimensions(),
            "advanced_settings": dict(self.advanced_settings),
            "bg_image_path": self.bg_image_path,
            "watermark_default_path": os.path.join(self.img_path, "default_watermark.png"),
            "allow_watermark": self.export_mode_var.get() != "Still Images",
        }
        work = [(j[0], j[1], j[2], params) for j in jobs]

        last_reported_pct = [-1.0]

        def _report(done):
            pct = p_start + p_span * (done / max(1, total_jobs))
            if pct - last_reported_pct[0] >= 0.01 or done == total_jobs:
                last_reported_pct[0] = pct
                self._ui_call(lambda p=pct: self.progress_bar.set(p))

        pool = None
        is_low_end_mode = bool(self.advanced_settings.get("performance_mode", False))
        self.set_export_priority(low_end_mode=is_low_end_mode)
        try:
            try:
                from concurrent.futures import ThreadPoolExecutor
                pool = ThreadPoolExecutor(max_workers=self.get_export_worker_count())
            except Exception as e:
                print(f"[SIGMAFLIP] Thread pool unavailable, falling back to single-process export: {e}")

            done = 0
            signed_count = 0
            if pool is not None:
                try:
                    from concurrent.futures import as_completed
                    futures = {pool.submit(_process_and_sign_job, w): w for w in work}
                    done = 0
                    for fut in as_completed(futures):
                        try:
                            ok = fut.result()
                        except Exception:
                            ok = False
                        if ok:
                            signed_count += 1
                        done += 1
                        _report(done)
                except Exception as e:
                    print(f"[SIGMAFLIP] Threaded export failed, falling back to single-process export: {e}")
                    pool.shutdown()
                    pool = None

            if pool is None:
                done = 0
                for w in work:
                    ok = _process_and_sign_job(w)
                    if ok:
                        signed_count += 1
                    done += 1
                    _report(done)
        finally:
            self.set_export_priority(low_end_mode=False)

        for src, new_filepath, _time_str, frame_time in jobs:
            if remove_sources and os.path.exists(src) and os.path.exists(new_filepath):
                try:
                    os.unlink(src)
                except Exception:
                    pass
            try:
                os.utime(new_filepath, (frame_time, frame_time))
            except Exception:
                pass

        return signed_count, len(folder_sets)

    def _commit_staged_export(self, staging_dir, final_dir):
        try:
            entries = os.listdir(staging_dir) if os.path.isdir(staging_dir) else []
            if not entries:
                shutil.rmtree(staging_dir, ignore_errors=True)
                return ""
            os.makedirs(final_dir, exist_ok=True)
            shutil.copytree(staging_dir, final_dir, dirs_exist_ok=True)
            shutil.rmtree(staging_dir, ignore_errors=True)
            return ""
        except Exception as e:
            self._ui_call(lambda err=e: messagebox.showerror(
                "Export Finalize Failure",
                f"Export finished, but the files could not be moved into:\n{final_dir}\n\n{err}\n\n"
                f"They remain in the temporary folder:\n{staging_dir}"
            ))
            return ""

    def _stage_and_commit(self, output_dir, final_dir, sources, progress_range):
        """Signs+partitions sources into the console layout, commits the staging dir, and
        reports the save. Shared tail of every export pipeline; returns what the caller
        needs to word its completion message."""
        base_time = time.time()
        append_info = self._find_append_starting_point(final_dir) if self.continuous_export else None
        signed_count, _folder_sets = self._sign_and_partition(
            output_dir, sources, base_time, remove_sources=False,
            progress_range=progress_range, append_info=append_info)
        self._ui_call(lambda: self.play_sound('save.mp3'))
        self._commit_staged_export(output_dir, final_dir)
        cache_note = self._cleanup_dsi_album_cache(final_dir) if self.console_type == "dsi" else ""
        return signed_count, append_info, cache_note

    def _find_append_starting_point(self, target_dir: str):
        """Where the numbering stopped in an existing export, so a new one continues it.

        Returns None when there is nothing to continue. Timestamps matter as much as the
        numbers: the DSi album is ordered by the date inside each photo, so the new frames
        have to carry the second after the newest existing one or they land at the wrong
        end of the timeline. Seeding that search with time.time() would date every appended
        frame "now" and open a gap the size of the gap between the two exports.
        """
        dsi_suffix = "NIN02" if self.console_type == "dsi" else "NIN01"
        use_parts = (self.export_structure == "parts")
        if not os.path.isdir(target_dir):
            return None

        def _scan_folder(path):
            """(highest HNI number, newest photo mtime, photo count) for one part folder."""
            top, newest, count = 0, 0.0, 0
            try:
                names = os.listdir(path)
            except OSError:
                return top, newest, count
            for name in names:
                upper = name.upper()
                if not (upper.startswith("HNI_") and upper.endswith(".JPG")):
                    continue
                try:
                    num = int(name[4:8])
                except ValueError:
                    continue
                count += 1
                top = max(top, num)
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(path, name)))
                except OSError:
                    pass
            return top, newest, count

        try:
            entries = os.listdir(target_dir)
        except OSError:
            return None

        slots = []
        if use_parts:
            for name in entries:
                m = re.match(r"^SFPart_(\d+)$", name)
                if m:
                    slots.append((1, int(m.group(1)), os.path.join(target_dir, name)))
        else:
            batches = []
            for name in entries:
                if name == "DCIM":
                    batches.append((1, name))
                else:
                    m = re.match(r"^DCIM_(\d+)$", name)
                    if m:
                        batches.append((int(m.group(1)), name))
            for batch_index, batch_name in sorted(batches):
                batch_dir = os.path.join(target_dir, batch_name)
                try:
                    subs = os.listdir(batch_dir)
                except OSError:
                    continue
                for sub in subs:
                    if len(sub) == 8 and sub[:3].isdigit() and sub.endswith(dsi_suffix):
                        slots.append((batch_index, int(sub[:3]), os.path.join(batch_dir, sub)))
        slots.sort(key=lambda slot: slot[:2])

        per_batch, resume, newest_any = {}, None, 0.0
        for batch_index, folder_idx, path in slots:
            top, newest, count = _scan_folder(path)
            newest_any = max(newest_any, newest)
            used = per_batch.get(batch_index, 0) + count
            per_batch[batch_index] = used
            if count:
                resume = {"batch_index": batch_index, "folder_idx": folder_idx,
                          "file_idx": top, "used_in_batch": used}
        if resume is None:
            return None
        resume["next_base_time"] = newest_any + 2.0
        return resume

    def _handle_existing_images_backup(self, target_dir):
        if self.continuous_export:
            return True, ""
        try:
            entries = [e for e in os.listdir(target_dir)
                       if e == "DCIM" or e.startswith("DCIM_") or e.startswith("SFPart_")]
        except OSError:
            entries = []
        if not entries:
            return True, ""
        if not messagebox.askyesno(
            "Existing Export Folders Found",
            f"The destination folder contains {len(entries)} previous export folder(s) "
            f"({', '.join(entries)}).\n\n"
            "Move them into a backup subfolder before exporting?\n\n"
            "(Enable Options > Continuous Export to append frames to them instead.)",
            parent=self.root
        ):
            self.play_sound('back.mp3')
            return False, ""
        backup_dir = os.path.join(target_dir, f"_SIGMAFLIP_exportbackup_{time.strftime('%Y%m%d_%H%M%S')}")
        try:
            os.makedirs(backup_dir, exist_ok=True)
            for e in entries:
                shutil.move(os.path.join(target_dir, e), os.path.join(backup_dir, e))
        except Exception as ex:
            self.play_sound('warning.mp3')
            messagebox.showerror(
                "Backup Failed",
                f"Previous export folders could not be moved into a backup folder:\n{ex}\n\nExport cancelled.",
                parent=self.root
            )
            return False, ""
        self.play_sound('apply.mp3')
        return True, f"\n\n{len(entries)} previous export folder(s) were moved into:\n{backup_dir}"

    def run_still_image_export_pipeline(self, output_dir: str, final_dir: str) -> None:
        try:
            self._exporting = True
            signed_count, append_info, cache_note = self._stage_and_commit(
                output_dir, final_dir, self.image_paths, (0.0, 1.0))

            img_word = "picture" if len(self.image_paths) == 1 else "pictures"
            action_desc = "appended" if append_info else "exported and structured"
            bk = getattr(self, "_export_backup_note", "")

            self._ui_call(lambda c=signed_count, note=cache_note, d=final_dir, bk=bk, w=img_word, act=action_desc: self._notify_export_complete(
                "Export Complete",
                f"Successfully {act} {c} {w} into the DCIM directory layout!"
                + "\n\nFiles were saved into your selected destination." + bk + note, d
            ))
        except Exception as e:
            self._ui_call(lambda: self.play_sound('warning.mp3'))
            self._ui_call(lambda err=e: messagebox.showerror("Export Failure", f"An error occurred during picture export:\n{str(err)}"))
        finally:
            self._exporting = False
            self._ui_call(lambda: self.toggle_widgets_interactive_state(enabled=True))
            self._ui_call(lambda: self.progress_bar.set(1.0))
            self._ui_call(lambda: self.update_frame_display())

    def export_frames(self) -> None:
        if not self.video_path and not self.image_paths:
            return
        if self.playing:
            self.toggle_play()

        if self.export_mode_var.get() == "Still Images":
            if not self.image_paths:
                return

            final_dir = filedialog.askdirectory(
                title="Select Destination Folder (e.g. SD Card Root)")
            if not final_dir:
                self.play_sound('back.mp3')
                return

            ok, bknote = self._handle_existing_images_backup(final_dir)
            if not ok:
                return
            self._export_backup_note = bknote

            staging_dir = tempfile.mkdtemp(prefix="sigmaflip_export_")
            self.progress_bar.set(0)
            self.toggle_widgets_interactive_state(enabled=False)

            threading.Thread(
                target=self.run_still_image_export_pipeline,
                args=(staging_dir, final_dir),
                daemon=True
            ).start()
            return

        final_dir = filedialog.askdirectory(title="Choose Output Export Directory (e.g. SD Card Root)")
        if not final_dir:
            self.play_sound('back.mp3')
            return
        ok, bknote = self._handle_existing_images_backup(final_dir)
        if not ok:
            return
        self._export_backup_note = bknote
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            self.play_sound('warning.mp3')
            messagebox.showerror(
                "Export Error", "FFmpeg executable was not found on your system PATH.\n\nPlease install FFmpeg to run export pipelines."
            )
            return

        staging_dir = tempfile.mkdtemp(prefix="sigmaflip_export_")
        self.progress_bar.set(0)
        self.toggle_widgets_interactive_state(enabled=False)

        export_bg_type = self.bg_type_var.get()
        export_thread = threading.Thread(
            target=self.run_ffmpeg_export_pipeline,
            args=(staging_dir, final_dir, ffmpeg_bin, export_bg_type),
            daemon=True
        )
        export_thread.start()

    def run_ffmpeg_export_pipeline(self, output_dir: str, final_dir: str, ffmpeg_path: str, bg_type: str = "black") -> None:
        target_fps = SPEED_FPS[self.speed]
        exact_frames = self.get_exact_export_frame_count()

        temp_dir = tempfile.mkdtemp(prefix="sigmaflip_export_")
        temp_pattern = os.path.join(temp_dir, "frame_%05d.png")

        cmd = [
            ffmpeg_path, "-y", "-i", os.path.abspath(self.video_path),
            "-vf", f"fps=fps={target_fps}:round=near,tpad=stop_mode=clone:stop=-1",
            "-frames:v", str(exact_frames),
            "-an", temp_pattern
        ]

        try:
            self._exporting = True
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, creationflags=_NO_WINDOW)
            stderr_tail = []
            last_frame_pct = -1.0
            while True:
                line = process.stderr.readline()
                if not line:
                    break
                stderr_tail.append(line.rstrip("\n"))
                stderr_tail = stderr_tail[-12:]
                if "frame=" in line:
                    try:
                        parts = line.split("frame=")[1].strip().split()
                        curr_exported_frame = int(parts[0])
                        progress_val = min(0.05, (curr_exported_frame / max(1, exact_frames)) * 0.05)
                        if progress_val - last_frame_pct >= 0.001:
                            last_frame_pct = progress_val
                            self._ui_call(lambda p=progress_val: self.progress_bar.set(p))
                    except Exception:
                        pass
            process.wait()

            temp_filenames = sorted([
                f for f in os.listdir(temp_dir)
                if f.lower().endswith(".png")
            ]) if os.path.isdir(temp_dir) else []

            if temp_filenames and len(temp_filenames) < exact_frames:
                last_file = os.path.join(temp_dir, temp_filenames[-1])
                for pad_idx in range(len(temp_filenames) + 1, exact_frames + 1):
                    pad_name = f"frame_{pad_idx:05d}.png"
                    if not os.path.exists(os.path.join(temp_dir, pad_name)):
                        shutil.copy2(last_file, os.path.join(temp_dir, pad_name))
                temp_filenames = sorted([
                    f for f in os.listdir(temp_dir)
                    if f.lower().endswith(".png")
                ])

            if process.returncode == 0 and temp_filenames:
                self._ui_call(lambda: self.file_name_label.configure(text="Timestamping, Signing & Splitting...", text_color=MAIN_COLOR))

                sources = [os.path.join(temp_dir, f) for f in temp_filenames]
                signed_count, append_info, cache_note = self._stage_and_commit(
                    output_dir, final_dir, sources, (0.05, 1.0))

                self._ui_call(lambda: self.file_name_label.configure(text=os.path.basename(self.video_path), text_color=self.sub_color_adaptive))
                mode_desc = "appended to the existing export" if append_info else "exported"
                bk = getattr(self, "_export_backup_note", "")
                self._ui_call(lambda c=signed_count, note=cache_note, d=final_dir, bk=bk, m=mode_desc: self._notify_export_complete(
                    "Export Complete",
                    f"Successfully {m} ({c} frames) into the selected directory layout!"
                    + "\n\nFiles were saved into your selected folder." + bk + note, d
                ))
            else:
                detail = "No frames were written." if process.returncode == 0 else f"FFmpeg returned code {process.returncode}."
                self._ui_call(lambda: self.play_sound('warning.mp3'))
                self._ui_call(lambda d=f"{detail}\n\nFFmpeg output (last lines):\n{chr(10).join(stderr_tail) or '(none)'}": messagebox.showerror("Export Failed", d))
        except Exception as e:
            self._ui_call(lambda: self.play_sound('warning.mp3'))
            self._ui_call(lambda err=e: messagebox.showerror("Pipeline Failure", f"An error occurred:\n{str(err)}"))
        finally:
            self._exporting = False
            shutil.rmtree(temp_dir, ignore_errors=True)
            self._ui_call(lambda: self.toggle_widgets_interactive_state(enabled=True))
            self._ui_call(lambda: self.progress_bar.set(1.0))
            self._ui_call(lambda: self.update_frame_display())

    def toggle_widgets_interactive_state(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.options_menu.entryconfigure(self.adv_menu_index, state=state)
        self._set_icon(self.load_btn, 'upload', state)
        if self.export_mode_var.get() == "Still Images":
            self.play_btn.configure(state="disabled")
        else:
            self._set_icon(self.play_btn, 'play', state)
        self.export_btn.configure(state=state)
        self.aspect_menu.configure(state=state)
        self.export_mode_menu.configure(state=state)
        self.beg_btn.configure(state=state)
        self.prev_btn.configure(state=state)
        self.next_btn.configure(state=state)
        self.end_btn.configure(state=state)
        if hasattr(self, 'tile_link_btn'):
            tile_icon = 'lock' if self.tile_link_locked else 'unlock'
            if not enabled:
                tile_icon = f"{tile_icon}_disabled"
            self._set_icon(self.tile_link_btn, tile_icon, state)
        if hasattr(self, 'tile_cols_entry'):
            self.tile_cols_entry.configure(state=state)
        if hasattr(self, 'tile_rows_entry'):
            self.tile_rows_entry.configure(state=state)
        if hasattr(self, 'timeline_slider'):
            self.timeline_slider.configure(state=state)
        self.sync_speed_widget_image(enabled and not self.playing)

    def on_close(self) -> None:
        self._cleanup_audio()

        if self.cap:
            self.cap.release()
        if self.gif_img:
            self.gif_img.close()
        if self.after_play_id:
            self.root.after_cancel(self.after_play_id)
        if self._preview_refresh_id:
            self.root.after_cancel(self._preview_refresh_id)

        self.save_user_settings()
        self.root.destroy()
