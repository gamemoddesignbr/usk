# μsk — Logo Replacement Guide

The μsk PicoFly firmware displays a splash screen on the Nintendo Switch when no SD card is
detected. This screen shows a flying Raspberry Pi logo plus "No SD Card" text. This guide covers
everything needed to understand, extract, and replace that logo.

---

## What is stored where

| Element | Stored in | How it is drawn |
|---------|-----------|-----------------|
| **Flying Raspberry Pi logo** | Compressed image in `payload.h` (this guide) | `draw_bitmap()` at framebuffer (0,0) |
| "No SD Card" text | Hardcoded strings + bitmap font in `payload.h` at offset `0x6224` | Text renderer |
| Battery indicator | Drawn by a separate routine | Drawing routine |

Only the graphical logo is replaced by this process. Text and battery indicator are unaffected.

---

## Image specification

| Property | Value |
|----------|-------|
| Width | **80 pixels** (fixed, hardware constraint) |
| Height | **256 pixels** (fixed, hardware constraint) |
| Color mode | **8-bit grayscale** — palette index N = gray shade N |
| Background value | **`0xF8`** (248 = near-white) |
| Useful logo range | `0x00` (black) … `0xF7` (lighter gray) |
| Input formats | PNG, BMP, JPG, or any format Pillow supports |

**Color support:** None. The display CLUT (Color Look-Up Table) at DC register `0x54201400` is
configured as a linear grayscale ramp (index N → shade N). There is no way to display color
without also patching the CLUT setup code.

**Background:** Pixel value `0xF8` is treated as "background". The framebuffer is pre-filled
with `0xF8` before drawing, so any pixel you leave as `0xF8` will blend invisibly.

**Current logo position:** The original flying Raspberry Pi occupies rows 72–183 (112 rows tall)
and roughly columns 3–55 of the 80-pixel-wide buffer. You can place a new logo anywhere in the
full 80×256 area.

**Display context:** The 80-pixel-wide logo buffer occupies the left portion of the 128-pixel-wide
framebuffer. The remaining 48 right-side columns stay at the `0xF8` background.

---

## Where the logo lives in payload.h

### Location
```
payload.h byte offsets:  0x54B8 – 0x5E87  (2512 bytes)
IRAM address at runtime: 0x40009278  (after payload self-relocation to 0x40004000)
```

### Block layout
```
Offset within block   Size    Content
0x0000 – 0x09BF       2500 B  Compressed pixel data (backward LZ stream)
0x09C0 – 0x09CB         12 B  Tail metadata (three uint32_t little-endian):
                                 t0 = 2512   ← total block size (data + tail)
                                 t1 =   12   ← controls decompressor start position
                                 t2 = 17968  ← t2 = decompressed_size – t0 = 20480 – 2512
```

### Size constraint — IMPORTANT
The calling code at payload offset `0x051E` (`IRAM 0x400042DE`) hardcodes the block size:
```asm
movs  r1, #0x9D       ; 157
lsls  r1, r1, #4      ; r1 = 2512  ← hardcoded, do NOT change
```
**The replacement block must be exactly 2512 bytes.** If your compressed data is shorter than
2500 bytes (it almost always will be), `logo_tool.py` pads it with zeros automatically.

---

## Compression format

The 20 480-byte (80 × 256) pixel buffer is stored as a **custom backward LZ** compressed stream.

### Algorithm summary
- The decompressor processes the output buffer from **end to start** (backwards).
- Compressed stream is read from **high address to low address**.
- The stream interleaves control bytes and data bytes on the **same pointer** (`r3`).
- Each control byte describes 8 operations (one bit per operation, high bit first):
  - **Bit = 0 → literal:** consume 1 byte from stream, write to output.
  - **Bit = 1 → back-reference:** consume 2 bytes, decode as:
    - `val16 = lo_byte | (hi_byte << 8)`
    - `match_len = ((val16 >> 11) + 6) & ~1`  → range 6–36 (always even)
    - `offset   = (val16 & 0xFFF) + 3`         → range 3–4098
    - Copies `match_len` bytes from `buf[write_start + offset]` to `buf[write_start]`.

### Relevant payload functions
| Function | Payload offset | IRAM address |
|----------|---------------|--------------|
| Decompressor | `0x37B0` | `0x40007570` |
| Setup wrapper (calls decompressor) | `0x3840` | `0x40007600` |
| memcpy helper | — | `0x400090E8` |
| memset helper | — | `0x40009190` |

---

## Tool versions

| Script | Purpose |
|--------|---------|
| `tools/logo_tool.py` | Original tool |
| `tools/logo_tool2.py` | Same as above + wait messages before slow compression steps |
| `tools/logo_tool3.py` | Adds `--auto-crop`, `--high-contrast`, and `--threshold` flags for better results with real-world images |

**Use `logo_tool3.py`** for any new work. `logo_tool.py` and `logo_tool2.py` are kept for reference.

---

## Replacing the logo — step by step

### Prerequisites
```bash
pip install Pillow
```

### 1. Preview what your image will look like (no changes to files)
```bash
python tools/logo_tool3.py convert my_image.png preview.png
```
Saves `preview.png` (1×) and `preview_4x.png` (4× scale) and prints the estimated
compressed size. No files are modified.

**Useful flags:**
```bash
--invert          # for dark-background / light-logo source images
--auto-crop       # trim light border padding before scaling (fills more canvas)
--high-contrast   # threshold to pure black/white before scaling (crisper edges,
                  # much better compression for anti-aliased images)
--threshold N     # darkness cutoff for --auto-crop and --high-contrast (default: 128)
```

**Recommended starting point for most logos:**
```bash
python tools/logo_tool3.py convert my_image.png preview.png --auto-crop --high-contrast
```

### 2. Inject the new logo into payload.h
```bash
python tools/logo_tool3.py inject my_image.png --auto-crop --high-contrast
```
Use the same flags as your convert preview. A backup `payload.h.bak` is created automatically.

To specify a different payload.h location:
```bash
python tools/logo_tool3.py inject my_image.png --payload path/to/payload.h --auto-crop --high-contrast
```

### 3. Rebuild
Use the normal build process (GitHub Actions workflow or local CMake build).

### Extract the current logo (for reference)
```bash
python tools/logo_tool3.py extract
# → logo_extracted.png        (80×256, actual pixel values)
# → logo_extracted_4x.png     (320×1024, scaled up 4× for easy viewing)
```

---

## Tips for designing a replacement logo

- **Display is inverted:** The Switch screen renders `0xF8` (background) as dark gray and
  `0x00` (black in the buffer) as bright white. Design your logo as dark content on a light
  background — it will appear as light content on a dark screen.
- **Use `--invert`** if your source image has a dark background with a light logo.
- **Keep it simple:** Fewer unique pixel values compress better. Use `--high-contrast` to
  collapse anti-aliasing gradients to pure black/white — this can reduce compressed size from
  over 4000 bytes to under 1400 bytes for a typical logo with anti-aliasing.
- **No alpha channel:** Use `0xF8` for any area that should blend into the background.
- **Full 80 px width is safe:** The "No SD Card" text and battery indicator are drawn below
  the logo area, not beside it — a full-width logo will not overlap them.
- **Aspect ratio:** No constraint. You can fill the whole 80×256 canvas or use only part of it.
- **Text in the logo:** Possible, but it will be small (80 px wide). The existing "No SD Card"
  text is drawn at a different region — you can leave that area blank in your image.

---

## First-flash behavior after a logo change

After building and flashing new firmware with a different logo, the boot sequence differs from
normal operation. Understanding this prevents unnecessary force-shutdowns.

### Why the first boot is different

Every time `payload[]` changes (logo change), the stored CRC (`pcrc`) no longer matches.
The firmware detects this via `is_configured()` and rewrites the NAND before glitching.

Additionally, flashing a new UF2 overwrites the saved glitch offsets in flash (they live at
`0x8000`, within the busk.bin range). Without saved offsets, the firmware must do a full
random scan across all 87 offsets — this can take **30+ minutes**.

### Expected LED sequence

| LED | What is happening | Action |
|-----|-------------------|--------|
| White (long) | Writing BCT + payload to NAND | Wait — do NOT power off |
| Blue (pulsing, many minutes) | Random glitch scan — searching for working offset | Wait — do NOT force-shut |
| Yellow (blink pattern) | Full scan complete, no success — normal on first try | Power cycle and try again |
| Green | Glitch succeeded — working offset found and saved | Done |

After **one green boot**, all subsequent boots are fast (saved offset used directly).

### Critical: do not force-shut during blue phase

Force-shutting during the blue phase interrupts the scan before an offset is found. The
firmware will restart the full scan on the next boot. Let it run until yellow appears.

### What to expect across multiple boots

1. **Boot 1** (after UF2 flash): white → blue (many minutes) → yellow → power cycle
2. **Boot 2–N**: blue → yellow → power cycle  *(repeat until green)*
3. **First green boot**: new logo displayed, offset saved
4. **All subsequent boots**: fast (seconds), new logo shown

---

## Reference files

| File | Description |
|------|-------------|
| `logo_extracted.png` | Current logo extracted at 1× (80×256 px) |
| `logo_extracted_4x.png` | Current logo at 4× (320×1024 px) — easier to view |
| `tools/logo_tool.py` | Original extract / convert / inject script |
| `tools/logo_tool2.py` | Same + compression wait messages |
| `tools/logo_tool3.py` | Full-featured: adds `--auto-crop`, `--high-contrast`, `--threshold` |
| `tools/logo_current_buffer.bin` | Raw decompressed 80×256 buffer (20480 bytes) |

---

## Answering common questions

**Can I use a color image?**  
You can *input* a color image — the tool converts it to grayscale automatically. The display
itself only supports grayscale (the CLUT is a linear gray ramp). Color would require additional
payload patches.

**What file format should I prepare?**  
Any format Pillow supports (PNG, BMP, JPEG, TIFF, …). PNG is recommended. The tool handles all
conversion internally.

**Does size/aspect ratio matter?**  
Any size works — the tool resizes your image to fit within 80×256 (with letterboxing if needed).
The only hard constraints are width=80, height=256 for the final buffer.

**How long does compression take?**
The compressor is a Python O(n²) algorithm. Expect 5–15 seconds for a typical 80×256 logo image.
Simple images with large background areas compress faster.

**Will the compressed data always fit in 2512 bytes?**  
In practice, yes. The backward LZ compressor achieves around 8× compression on typical logo
images. Even a detailed 80×256 image with many shades compresses comfortably under 2500 bytes.
If it ever doesn't fit, `logo_tool.py` will report an error.

**What about the "No SD Card" text?**  
That text is rendered separately by the payload's text-drawing routines and is not part of this
image. Replacing the logo does not affect the text.
