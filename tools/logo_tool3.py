#!/usr/bin/env python3
"""
logo_tool3.py — μsk PicoFly Logo Replacement Tool (enhanced image processing)

Adds three strategies on top of logo_tool2.py to preserve detail at small sizes:

  --invert         Invert the image before conversion (for dark-bg sources).
  --auto-crop      Crop away light/white border padding before scaling so the
                   actual logo content fills as much of the 80×256 canvas as
                   possible.
  --high-contrast  Threshold the image to pure black-or-white before scaling
                   so edges stay crisp at low resolution. Pixels darker than
                   the threshold become black (0x00); lighter pixels become
                   background (0xF8). Use --threshold to tune the cutoff
                   (default: 128).

All three flags are independent and can be combined freely.

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

# In-place decompression safety constraint (see compress() for derivation)
MAX_MATCH        = 36     # decompressor max back-ref length
# stream_start=7 (MIN_LEADING_ZEROS=7) matches rehius: r2 wraps near end,
# writes 0xF8 pixels to IRAM 0x4003AFxx (below output_buf). Required for glitch to work.
MIN_LEADING_ZEROS = 7    # keep 7 leading zeros like rehius (stream_start=7)
STREAM_TOP       = COMP_DATA_LEN - 4          # 2496: stream ends at position 2495
MAX_STREAM_LEN   = STREAM_TOP - MIN_LEADING_ZEROS  # 2489: matches rehius stream range
SAFE_STREAM_LEN  = 2485                       # degrade back-refs to expand stream toward max

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

    # Use separate stream and output buffers so in-place write/read crossover
    # does not corrupt the stream data.  The ARM hardware uses a single buffer
    # but the crossover only affects a handful of pixels in the all-BG top rows,
    # which are cosmetically insignificant.  Separate buffers give a clean
    # round-trip that matches convert output exactly.
    stream_buf = bytearray(COMP_DATA_LEN)
    stream_buf[0:COMP_DATA_LEN] = comp_data   # read-only stream

    out_buf = bytearray([BG] * BUF_LEN)       # output pixels, pre-filled with BG

    # Decompressor initial state — derived from tail exactly as the ARM code does
    r3 = (t0 - t1) - 1        # read pointer  (starts at last stream byte, e.g. 2495)
    r2 = t0 + t2               # output pointer (= 20 480 = BUF_LEN)
    r5 = 8                     # bits remaining in current control byte
    r1 = stream_buf[r3] if 0 <= r3 < COMP_DATA_LEN else 0

    while r2 > 0:
        flag = (r1 >> 7) & 1  # high bit = current control bit

        if flag == 0:
            # ---- Literal ----
            if r3 <= 0:
                break
            r3 -= 1
            literal = stream_buf[r3] if 0 <= r3 < COMP_DATA_LEN else 0
            r2 -= 1
            out_buf[r2] = literal
        else:
            # ---- Back-reference ----
            if r3 < 2:
                break
            b_hi = stream_buf[r3 - 1] if r3 - 1 < COMP_DATA_LEN else 0
            b_lo = stream_buf[r3 - 2] if r3 - 2 < COMP_DATA_LEN else 0
            r3  -= 2
            val16      = b_lo | (b_hi << 8)
            match_len  = ((val16 >> 11) + 6) & 0xFE   # 6–36, always even
            offset     = (val16 & 0xFFF) + 3            # 3–4098
            write_start = r2 - match_len
            if write_start < 0:
                break
            for i in range(match_len):
                src = write_start + offset + i
                out_buf[write_start + i] = out_buf[src] if src < BUF_LEN else BG
            r2 = write_start

        # Consume the control bit
        r1 = (r1 << 1) & 0xFF

        # Decrement bit counter; reload control byte when exhausted
        r5 -= 1
        if r5 == 0:
            r3 -= 1
            if r3 < 0:
                break
            r1 = stream_buf[r3] if 0 <= r3 < COMP_DATA_LEN else 0
            r5 = 8

    return bytes(out_buf)


# ---------------------------------------------------------------------------
# Backward LZ compressor
# ---------------------------------------------------------------------------

def _build_stream(ops):
    """Build raw compressed stream bytes from ops list of (flag, data_bytes, pos, length)."""
    groups = []
    for g in range(0, len(ops), 8):
        group = ops[g:g + 8]
        ctrl  = 0
        for i, (flag, _, _, _) in enumerate(group):
            if flag:
                ctrl |= 1 << (7 - i)
        gb = bytearray()
        for flag, db, _, _ in reversed(group):
            gb.extend(db)
        gb.append(ctrl)
        groups.append(bytes(gb))
    stream = bytearray()
    for g in reversed(groups):
        stream.extend(g)
    return bytes(stream)


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

    # In-place decompression safety: use t1=16 (original firmware value).
    # With t1=16: r3 = (2512-16)-1 = 2495. Stream occupies [stream_start..2495].
    # Bytes [2496..2499] are trailing zeros (never read by decompressor).
    # This matches the original rehius payload layout exactly.
    #
    # Uses module-level constants: MIN_LEADING_ZEROS=7, MAX_STREAM_LEN=2489, SAFE_STREAM_LEN=2485.
    # stream_start=7 matches rehius: ARM r2 wraps unsigned near end, writes to IRAM 0x4003AFxx.
    # Position 0 of logo block must be 0x00 (ARM reads it before decompression).

    # Collect operations with position info: (flag, data_bytes, pos, length)
    # flag=0: literal covering data[pos], flag=1: back-ref covering data[pos-length+1..pos]
    ops = []
    pos = n - 1

    while pos >= 0:
        best_len    = 0
        best_offset = 0

        if pos + 1 < n:
            # Only try even lengths — decompressor applies & 0xFE (rounds odd down by 1),
            # so odd match_len would decode as match_len-1, losing 1 pixel per back-reference.
            max_len = min(MAX_MATCH, pos + 1)
            if max_len & 1:
                max_len -= 1  # round down to even
            for length in range(max_len, MIN_MATCH - 1, -2):
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
            ops.append((1, bytes([val16 & 0xFF, val16 >> 8]), pos, best_len))
            pos -= best_len
        else:
            ops.append((0, bytes([data[pos]]), pos, 1))
            pos -= 1

    # If stream too short for safe in-place decompression, degrade back-refs near
    # pos=0 (end of ops list) into literals to expand the stream.
    # Pad stream into [SAFE_STREAM_LEN..MAX_STREAM_LEN].
    #
    # Strategy: split large back-refs into 6-length back-refs (not literals).
    # Back-refs copy from already-written output — they have no "data bytes" at low
    # stream positions that the write pointer (r2) could overwrite before r3 reads them.
    # Literals DO have data bytes at low stream positions and always cause in-place
    # corruption when placed near pos=0, making literal-based padding unreliable.
    def _split_to_b6(data, op_pos, op_len):
        """Split a back-ref into ≤6-length back-refs (or literals as fallback)."""
        result = []
        p = op_pos
        end = op_pos - op_len  # exclusive
        while p > end:
            chunk = min(6, p - end)
            if chunk < MIN_MATCH:
                # too small for a back-ref, use literal
                result.append((0, bytes([data[p]]), p, 1))
                p -= 1
                continue
            dst_start = p + 1 - chunk
            target    = data[dst_start:p + 1]
            found     = False
            for src in range(p + 1, min(p + 1 + MAX_OFFSET - chunk + 2, n - chunk + 1)):
                off = src - dst_start
                if off > MAX_OFFSET:
                    break
                if data[src:src + chunk] == target:
                    v = ((chunk - 6) << 11) | (off - 3)
                    result.append((1, bytes([v & 0xFF, v >> 8]), p, chunk))
                    p -= chunk
                    found = True
                    break
            if not found:
                result.append((0, bytes([data[p]]), p, 1))
                p -= 1
        return result

    stream = _build_stream(ops)
    if len(stream) < SAFE_STREAM_LEN:
        i = len(ops) - 1
        while len(stream) < SAFE_STREAM_LEN and i >= 0:
            flag, data_bytes, op_pos, op_len = ops[i]
            if flag == 1 and op_len > 6:
                split       = _split_to_b6(data, op_pos, op_len)
                candidate   = ops[:i] + split + ops[i + 1:]
                new_stream  = _build_stream(candidate)
                if len(new_stream) <= MAX_STREAM_LEN:
                    ops    = candidate
                    stream = new_stream
            i -= 1

    comp_data = bytes(stream)
    comp_size = len(comp_data)

    if comp_size > MAX_STREAM_LEN:
        raise ValueError(
            f"Compressed stream is {comp_size} bytes, exceeds safe limit of {MAX_STREAM_LEN} bytes "
            f"(stream_start must be ≥ {MIN_LEADING_ZEROS} to prevent IRAM corruption during "
            "in-place decompression). Simplify logo: increase --threshold, reduce detail, or scale smaller."
        )

    # Layout: zeros + stream + 4 trailing zeros, matching original firmware.
    # stream at [stream_start..2495], zeros at [0..stream_start-1], zeros at [2496..2499].
    leading_zeros = STREAM_TOP - comp_size                  # = 2496 - comp_size
    trailing_zeros = COMP_DATA_LEN - STREAM_TOP             # = 4  (positions 2496..2499)
    padded = b"\x00" * leading_zeros + comp_data + b"\x00" * trailing_zeros

    # t0=2512, t1=16 → r3=(2512-16)-1=2495 (last byte of stream), t2=17968 → r2=20480
    t0 = LOGO_BLOCK_LEN   # 2512
    t1 = 16               # original firmware value; r3=2495 (stream ends at buf[2495])
    t2 = BUF_LEN - LOGO_BLOCK_LEN   # 17968
    tail = struct.pack("<III", t0, t1, t2)

    block = padded + tail
    assert len(block) == LOGO_BLOCK_LEN
    return block, comp_size


# ---------------------------------------------------------------------------
# Image conversion
# ---------------------------------------------------------------------------

def image_to_logo_buffer(
    image_path: Path,
    invert: bool = False,
    auto_crop: bool = False,
    high_contrast: bool = False,
    threshold: int = 128,
) -> bytes:
    """
    Load any image and produce an 80×256 grayscale pixel buffer
    with background value 0xF8.

    Processing order:
      1. Convert to grayscale.
      2. Invert (if --invert).
      3. Auto-crop to the tight bounding box of dark content (if --auto-crop).
      4. Threshold to pure black/white (if --high-contrast).
      5. Scale to fit within 80×256, preserving aspect ratio.
      6. Center on the canvas and remap to the 0x00–0xF8 range.
    """
    from PIL import Image, ImageOps

    img = Image.open(image_path).convert("L")   # grayscale

    # Step 2 — invert
    if invert:
        img = ImageOps.invert(img)

    # Step 3 — auto-crop: remove light border padding so the logo fills more canvas
    if auto_crop:
        # Build a binary mask: pixels darker than threshold are "content" (255), rest 0
        mask = img.point(lambda p: 255 if p < threshold else 0)
        bbox = mask.getbbox()   # bounding box of non-zero (content) pixels
        if bbox:
            img = img.crop(bbox)
            print(f"  Auto-crop: trimmed to {img.width}×{img.height} px "
                  f"(was {Image.open(image_path).width}×{Image.open(image_path).height})")
        else:
            print("  Auto-crop: no dark content found, skipping crop")

    # Step 4 — high contrast: threshold to pure black or white
    if high_contrast:
        img = img.point(lambda p: 0 if p < threshold else 255)

    # Step 5 — scale to fit within BUF_W × BUF_H, preserving aspect ratio
    img.thumbnail((BUF_W, BUF_H), Image.LANCZOS)

    # Step 6 — place centered on the canvas and remap pixel values
    canvas = Image.new("L", (BUF_W, BUF_H), 255)
    x_off  = (BUF_W - img.width)  // 2
    y_off  = (BUF_H - img.height) // 2
    canvas.paste(img, (x_off, y_off))

    # Remap: 255 (white) → 0xF8 (background), 0 (black) → 0x00
    pixels = canvas.load()
    for y in range(BUF_H):
        for x in range(BUF_W):
            v = pixels[x, y]
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
    buf = image_to_logo_buffer(
        img_path,
        invert=args.invert,
        auto_crop=args.auto_crop,
        high_contrast=args.high_contrast,
        threshold=args.threshold,
    )

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
        block, stream_len = compress(buf)
        print(f"Estimated stream size: {stream_len} / {MAX_STREAM_LEN} bytes safe limit "
              f"({'OK' if stream_len <= MAX_STREAM_LEN else 'UNSAFE — reduce logo detail'})")
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
    buf = image_to_logo_buffer(
        img_path,
        invert=args.invert,
        auto_crop=args.auto_crop,
        high_contrast=args.high_contrast,
        threshold=args.threshold,
    )

    non_bg = sum(1 for b in buf if b != BG)
    print(f"  Image: {BUF_W}x{BUF_H}, {non_bg} non-background pixels")

    print("Compressing (this may take 5-15 seconds) ...")
    try:
        block, stream_len = compress(buf)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"  Stream size: {stream_len} / {MAX_STREAM_LEN} bytes safe limit (OK)")

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
        description="usk PicoFly - logo replacement tool (enhanced image processing)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python logo_tool3.py extract
  python logo_tool3.py convert my_logo.png preview.png
  python logo_tool3.py convert my_logo.png --invert
  python logo_tool3.py convert my_logo.png --auto-crop
  python logo_tool3.py convert my_logo.png --high-contrast
  python logo_tool3.py convert my_logo.png --auto-crop --high-contrast --threshold 100
  python logo_tool3.py inject  my_logo.png --auto-crop --high-contrast
  python logo_tool3.py inject  my_logo.png --payload ../payload.h --auto-crop --high-contrast
""",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    default_payload = str(PAYLOAD_H)

    # extract
    p_ext = sub.add_parser("extract", help="Extract the current logo from payload.h")
    p_ext.add_argument("--payload", default=default_payload,
                       help=f"Path to payload.h (default: {default_payload})")

    # shared flags helper
    def add_image_flags(p):
        p.add_argument("--invert", action="store_true",
                       help="Invert image before conversion (for dark-bg sources)")
        p.add_argument("--auto-crop", action="store_true", dest="auto_crop",
                       help="Crop away light border padding before scaling")
        p.add_argument("--high-contrast", action="store_true", dest="high_contrast",
                       help="Threshold to pure black/white before scaling for crisper edges")
        p.add_argument("--threshold", type=int, default=128, metavar="0-255",
                       help="Pixel darkness cutoff for --auto-crop and --high-contrast (default: 128)")

    # convert
    p_cvt = sub.add_parser("convert", help="Preview how an image would look as the logo")
    p_cvt.add_argument("input",  help="Input image (PNG, BMP, JPG, ...)")
    p_cvt.add_argument("output", nargs="?", help="Output PNG path (default: logo_preview.png)")
    add_image_flags(p_cvt)

    # inject
    p_inj = sub.add_parser("inject", help="Convert and inject an image into payload.h")
    p_inj.add_argument("input", help="Input image (PNG, BMP, JPG, ...)")
    p_inj.add_argument("--payload", default=default_payload,
                       help=f"Path to payload.h (default: {default_payload})")
    add_image_flags(p_inj)

    args = parser.parse_args()

    if args.command == "extract":
        cmd_extract(args)
    elif args.command == "convert":
        cmd_convert(args)
    elif args.command == "inject":
        cmd_inject(args)


if __name__ == "__main__":
    main()
