#!/usr/bin/env python3
"""
logo_tool.py — μsk PicoFly Logo Replacement Tool

Commands:
  extract  Extract the current logo from payload.h to PNG files.
  convert  Convert any image to the correct logo format (preview, no changes).
  inject   Convert an image and inject it into payload.h.

Requirements: pip install Pillow
"""

import argparse
import re
import struct
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAYLOAD_H      = Path(__file__).parent.parent / "payload.h"

# Location of the compressed logo block inside the payload byte array
LOGO_OFFSET    = 0x54B8   # Start of compressed data
LOGO_TAIL_OFF  = 0x5E7C   # Start of 12-byte tail  (= LOGO_OFFSET + 2500)
LOGO_END       = 0x5E88   # First byte after the block (= LOGO_TAIL_OFF + 12)
LOGO_BLOCK_LEN = LOGO_END - LOGO_OFFSET   # = 2512 bytes (DO NOT CHANGE)
COMP_DATA_LEN  = LOGO_TAIL_OFF - LOGO_OFFSET  # = 2500 bytes of compressed data

# Output buffer dimensions
BUF_W   = 80
BUF_H   = 256
BUF_LEN = BUF_W * BUF_H   # 20 480 bytes

# Pixel value for "background" (near-white)
BG = 0xF8


# ---------------------------------------------------------------------------
# payload.h I/O
# ---------------------------------------------------------------------------

def load_payload(path: Path) -> bytes:
    """Parse a payload.h C byte array and return raw bytes."""
    text = path.read_text(errors="replace")
    hex_vals = re.findall(r"0x([0-9a-fA-F]{2})", text)
    if len(hex_vals) < LOGO_END:
        raise ValueError(
            f"payload.h too short: found {len(hex_vals)} bytes, need at least {LOGO_END}"
        )
    return bytes(int(v, 16) for v in hex_vals)


def patch_payload_h(path: Path, new_block: bytes) -> None:
    """Replace the 2512-byte logo block in payload.h with new_block."""
    if len(new_block) != LOGO_BLOCK_LEN:
        raise ValueError(f"new_block must be exactly {LOGO_BLOCK_LEN} bytes, got {len(new_block)}")

    text = path.read_text(errors="replace")

    # Locate the payload[] array specifically.
    # Using index("{") + rindex("}") would span the entire file and destroy the
    # other constants (erista_bct_sign[], erista_bct_sd_sign[]) that follow it.
    PAYLOAD_DECL = "const unsigned char payload[] = {"
    array_start  = text.index(PAYLOAD_DECL) + len(PAYLOAD_DECL)  # char after opening {
    array_end    = text.index("};", array_start)                  # first }; after payload[] opens

    # Extract and patch hex values within payload[] only
    hex_vals = re.findall(r"0x[0-9a-fA-F]{2}", text[array_start:array_end])
    if len(hex_vals) < LOGO_END:
        raise ValueError("payload.h too short")

    hex_vals = list(hex_vals)
    for i, b in enumerate(new_block):
        hex_vals[LOGO_OFFSET + i] = f"0x{b:02x}"

    # Rebuild the array body with 16 bytes per line
    lines = []
    for i in range(0, len(hex_vals), 16):
        chunk = hex_vals[i:i + 16]
        lines.append(", ".join(chunk) + ", ")
    array_body = "\n".join(lines)

    # Replace only the payload[] body — everything after }; is left intact
    new_text = text[:array_start] + "\n" + array_body + "\n" + text[array_end:]
    path.write_text(new_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Backward LZ decompressor (pure Python, mirrors ARM decompressor at 0x40007570)
# ---------------------------------------------------------------------------

def decompress(payload_bytes: bytes) -> bytes:
    """Decompress the logo block from payload bytes. Returns 20 480 raw pixels."""
    comp_data = payload_bytes[LOGO_OFFSET:LOGO_TAIL_OFF]   # 2500 bytes
    tail      = payload_bytes[LOGO_TAIL_OFF:LOGO_END]       # 12 bytes
    t0, t1, t2 = struct.unpack("<III", tail)

    # Setup: copy compressed data to start of output buffer, zero-fill the rest
    buf = bytearray(BUF_LEN)
    buf[0:COMP_DATA_LEN] = comp_data

    # Decompressor initial state — derived from tail exactly as the ARM code does
    r3 = (t0 - t1) - 1        # read pointer  (= comp_size - 1 for our data, 2495 for original)
    r2 = t0 + t2               # output pointer (= 20 480 = BUF_LEN)
    r5 = 8                     # bits remaining in current control byte
    r1 = buf[r3]               # current control byte

    while r2 > 0:
        flag = (r1 >> 7) & 1  # high bit = current control bit

        if flag == 0:
            # ---- Literal ----
            if r3 <= 0:
                break
            r3 -= 1
            literal = buf[r3]
            r2 -= 1
            buf[r2] = literal
        else:
            # ---- Back-reference ----
            if r3 < 2:
                break
            b_hi = buf[r3 - 1]
            b_lo = buf[r3 - 2]
            r3  -= 2
            val16      = b_lo | (b_hi << 8)
            match_len  = ((val16 >> 11) + 6) & 0xFE   # 6–36, always even
            offset     = (val16 & 0xFFF) + 3            # 3–4098
            write_start = r2 - match_len
            if write_start < 0:
                break
            for i in range(match_len):
                src = write_start + offset + i
                buf[write_start + i] = buf[src] if src < BUF_LEN else BG
            r2 = write_start

        # Consume the control bit
        r1 = (r1 << 1) & 0xFF

        # Decrement bit counter; reload control byte when exhausted
        r5 -= 1
        if r5 == 0:
            r3 -= 1
            if r3 < 0:
                break
            r1 = buf[r3]
            r5 = 8

    return bytes(buf)


# ---------------------------------------------------------------------------
# Backward LZ compressor
# ---------------------------------------------------------------------------

def compress(data: bytes) -> bytes:
    """
    Compress an 80×256 pixel buffer using the backward LZ format.
    Returns a 2512-byte block (2500 bytes padded data + 12-byte tail).
    Raises ValueError if the compressed data exceeds 2500 bytes.
    """
    n          = len(data)
    MIN_MATCH  = 6
    MAX_MATCH  = 36
    MIN_OFFSET = 3
    MAX_OFFSET = 4098

    # Collect operations, processing from pos=n-1 downward
    ops = []
    pos = n - 1

    while pos >= 0:
        best_len    = 0
        best_offset = 0

        if pos + 1 < n:
            # Search for longest match in already-processed region data[pos+1:]
            for length in range(min(MAX_MATCH, pos + 1), MIN_MATCH - 1, -2):
                dst_start = pos + 1 - length
                if dst_start < 0:
                    continue
                target = data[dst_start:dst_start + length]
                for src_start in range(
                    pos + 1,
                    min(pos + 1 + MAX_OFFSET - length + 2, n - length + 1),
                ):
                    off = src_start - dst_start
                    if off > MAX_OFFSET:
                        break
                    if data[src_start:src_start + length] == target:
                        if length > best_len:
                            best_len    = length
                            best_offset = off
                        break
                if best_len >= MIN_MATCH:
                    break  # found best length, stop trying shorter

        if best_len >= MIN_MATCH:
            val16 = ((best_len - 6) << 11) | (best_offset - 3)
            ops.append((1, bytes([val16 & 0xFF, val16 >> 8])))
            pos -= best_len
        else:
            ops.append((0, bytes([data[pos]])))
            pos -= 1

    # Build the compressed stream
    # ops[0] covers the END of original data → must be at HIGHEST stream address
    # (decompressor reads ctrl bytes from high to low)
    #
    # Within each 8-op group:
    #   op[0] → bit 7 of ctrl, data bytes just below ctrl (highest data position)
    #   op[7] → bit 0 of ctrl, data bytes furthest below ctrl
    groups = []
    for g in range(0, len(ops), 8):
        group = ops[g:g + 8]
        ctrl  = 0
        for i, (flag, _) in enumerate(group):
            if flag:
                ctrl |= 1 << (7 - i)
        gb = bytearray()
        for flag, db in reversed(group):   # op[7] first → op[0] just before ctrl
            gb.extend(db)
        gb.append(ctrl)
        groups.append(bytes(gb))

    # groups[0] covers the end of data → must be LAST in stream (highest address)
    stream = bytearray()
    for g in reversed(groups):
        stream.extend(g)

    comp_data = bytes(stream)
    comp_size = len(comp_data)

    if comp_size > COMP_DATA_LEN:
        raise ValueError(
            f"Compressed data is {comp_size} bytes, exceeds budget of {COMP_DATA_LEN} bytes. "
            "Simplify the image (reduce detail, use fewer shades, or increase background area)."
        )

    # Stream at HIGH end, zeros at LOW end — matches original payload layout
    padded = b"\x00" * (COMP_DATA_LEN - comp_size) + comp_data

    # t0 = 2512 constant, t1=12 → r3=2499 (last byte of stream)
    t0 = LOGO_BLOCK_LEN
    t1 = LOGO_BLOCK_LEN - COMP_DATA_LEN   # 12
    t2 = BUF_LEN - LOGO_BLOCK_LEN         # 17968
    tail = struct.pack("<III", t0, t1, t2)

    block = padded + tail
    assert len(block) == LOGO_BLOCK_LEN
    return block


# ---------------------------------------------------------------------------
# Image conversion
# ---------------------------------------------------------------------------

def image_to_logo_buffer(image_path: Path, invert: bool = False) -> bytes:
    """
    Load any image and produce an 80×256 grayscale pixel buffer
    with background value 0xF8.

    The image is converted to grayscale, scaled to fit within 80×256
    while preserving aspect ratio (letterboxed), and centered.

    Dark pixels in the input → dark pixel values in the buffer (0x00 = black).
    Light/white areas → 0xF8 (background).

    If invert=True, the image is inverted before conversion (useful when your
    source is dark background / light logo rather than light background / dark logo).
    """
    from PIL import Image

    img = Image.open(image_path).convert("L")   # grayscale

    if invert:
        from PIL import ImageOps
        img = ImageOps.invert(img)

    # Scale to fit within BUF_W × BUF_H, preserving aspect ratio
    img.thumbnail((BUF_W, BUF_H), Image.LANCZOS)

    # Create background canvas and paste centered
    canvas = Image.new("L", (BUF_W, BUF_H), BG)
    x_off  = (BUF_W - img.width)  // 2
    y_off  = (BUF_H - img.height) // 2
    canvas.paste(img, (x_off, y_off))

    # Re-map pixel values: map pure white (255) → 0xF8 (background), scale dark pixels
    pixels = canvas.load()
    for y in range(BUF_H):
        for x in range(BUF_W):
            v = pixels[x, y]
            # Scale: 255 (white) → 0xF8, 0 (black) → 0x00
            pixels[x, y] = round(v * BG / 255)

    return bytes(canvas.tobytes())


def buffer_to_image(buf: bytes, scale: int = 1):
    """Convert a raw 80×256 pixel buffer to a PIL Image."""
    from PIL import Image
    img = Image.frombytes("L", (BUF_W, BUF_H), buf[:BUF_LEN])
    if scale > 1:
        img = img.resize((BUF_W * scale, BUF_H * scale), Image.NEAREST)
    return img


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def cmd_extract(args):
    payload_path = Path(args.payload)
    print(f"Loading {payload_path} ...")
    payload_bytes = load_payload(payload_path)

    print("Decompressing logo ...")
    buf = decompress(payload_bytes)

    non_bg = sum(1 for b in buf if b != BG)
    print(f"  Buffer: {BUF_W}x{BUF_H} pixels, {non_bg} non-background pixels")

    out1 = Path("logo_extracted.png")
    out4 = Path("logo_extracted_4x.png")
    buffer_to_image(buf, scale=1).save(out1)
    buffer_to_image(buf, scale=4).save(out4)
    print(f"Saved: {out1}  (actual size)")
    print(f"Saved: {out4}  (4× preview)")


def cmd_convert(args):
    from PIL import Image
    img_path     = Path(args.input)
    out_path     = Path(args.output) if args.output else Path("logo_preview.png")

    print(f"Converting {img_path} ...")
    buf = image_to_logo_buffer(img_path, invert=args.invert)

    # Save output
    preview = buffer_to_image(buf, scale=1)
    preview.save(out_path)
    print(f"Saved preview: {out_path}  ({BUF_W}x{BUF_H})")

    big_path = out_path.with_stem(out_path.stem + "_4x")
    buffer_to_image(buf, scale=4).save(big_path)
    print(f"Saved preview: {big_path}  (4× scale)")

    # Compression estimate
    print("Estimating compressed size (this may take 5-15 seconds) ...")
    try:
        block     = compress(buf)
        actual_comp_size = len(block[:COMP_DATA_LEN].lstrip(b" "))
        print(f"Estimated compressed size: {len(comp_data)} / {COMP_DATA_LEN} bytes "
              f"({len(comp_data)/COMP_DATA_LEN*100:.1f}% of budget used)")
    except ValueError as e:
        print(f"WARNING: {e}")

    # Try to show preview (optional)
    try:
        preview_big = buffer_to_image(buf, scale=4)
        preview_big.show()
    except Exception:
        pass


def cmd_inject(args):
    payload_path = Path(args.payload)
    img_path     = Path(args.input)

    print(f"Converting {img_path} ...")
    buf = image_to_logo_buffer(img_path, invert=args.invert)

    non_bg = sum(1 for b in buf if b != BG)
    print(f"  Image: {BUF_W}x{BUF_H}, {non_bg} non-background pixels")

    print("Compressing (this may take 5-15 seconds) ...")
    try:
        block = compress(buf)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    actual_comp_size = len(block[:COMP_DATA_LEN].lstrip(b" "))
    print(f"  Compressed: {actual_comp_size} / {COMP_DATA_LEN} bytes used")

    # Backup
    bak_path = payload_path.with_suffix(".h.bak")
    import shutil
    shutil.copy(payload_path, bak_path)
    print(f"  Backup saved: {bak_path}")

    # Patch
    print(f"Patching {payload_path} ...")
    payload_bytes = load_payload(payload_path)
    patch_payload_h(payload_path, block)
    print("Done. Rebuild the firmware to apply the change.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="usk PicoFly - logo replacement tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python logo_tool.py extract
  python logo_tool.py convert my_logo.png preview.png
  python logo_tool.py convert my_logo.png --invert   # for dark-bg images
  python logo_tool.py inject  my_logo.png
  python logo_tool.py inject  my_logo.png --payload ../payload.h
""",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    default_payload = str(PAYLOAD_H)

    # extract
    p_ext = sub.add_parser("extract", help="Extract the current logo from payload.h")
    p_ext.add_argument("--payload", default=default_payload,
                       help=f"Path to payload.h (default: {default_payload})")

    # convert
    p_cvt = sub.add_parser("convert", help="Preview how an image would look as the logo")
    p_cvt.add_argument("input",  help="Input image (PNG, BMP, JPG, ...)")
    p_cvt.add_argument("output", nargs="?", help="Output PNG path (default: logo_preview.png)")
    p_cvt.add_argument("--invert", action="store_true",
                       help="Invert image before conversion (for light-on-dark sources)")

    # inject
    p_inj = sub.add_parser("inject", help="Convert and inject an image into payload.h")
    p_inj.add_argument("input", help="Input image (PNG, BMP, JPG, ...)")
    p_inj.add_argument("--payload", default=default_payload,
                       help=f"Path to payload.h (default: {default_payload})")
    p_inj.add_argument("--invert", action="store_true",
                       help="Invert image before conversion")

    args = parser.parse_args()

    if args.command == "extract":
        cmd_extract(args)
    elif args.command == "convert":
        cmd_convert(args)
    elif args.command == "inject":
        cmd_inject(args)


if __name__ == "__main__":
    main()
