"""
Multi-Resolution Image Resizer
==============================
- Reads target resolutions from .env
- Supports multiple resize strategies:
  - cover: fill target and crop excess
  - cropping: alias for cover
  - pad: keep full content and add padding to reach the exact target size
  - contain: keep full content without padding (output may be smaller)
- Supports configurable output compression and export format conversion
- Supports custom output folder labels and prefixes
- Resizes transparent images with premultiplied alpha to avoid dark edge halos

Install dependencies:
    pip install Pillow python-dotenv

Usage:
    1. Put your source images in the input/ folder
    2. Edit .env to set your target resolutions
    3. Run: python resize_images.py
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageOps

BASE_DIR = Path(__file__).resolve().parent
ENV_CANDIDATES = (BASE_DIR / ".env", BASE_DIR / "env")
SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
SEPARATOR = "-" * 60

if hasattr(Image, "Resampling"):
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
else:
    RESAMPLE_LANCZOS = Image.LANCZOS

if hasattr(Image, "Dither"):
    DITHER_NONE = Image.Dither.NONE
    DITHER_FLOYDSTEINBERG = Image.Dither.FLOYDSTEINBERG
else:
    DITHER_NONE = Image.NONE
    DITHER_FLOYDSTEINBERG = Image.FLOYDSTEINBERG

if hasattr(Image, "Quantize"):
    QUANTIZE_MEDIANCUT = Image.Quantize.MEDIANCUT
    QUANTIZE_FASTOCTREE = Image.Quantize.FASTOCTREE
else:
    QUANTIZE_MEDIANCUT = Image.MEDIANCUT
    QUANTIZE_FASTOCTREE = Image.FASTOCTREE


def load_environment() -> Path | None:
    """Load .env first, then fall back to env if present."""
    for candidate in ENV_CANDIDATES:
        if candidate.exists():
            load_dotenv(candidate)
            return candidate

    load_dotenv()
    return None


def resolve_config_path(raw_path: str, default: str) -> Path:
    """Resolve relative paths from the project directory."""
    path = Path(raw_path or default)
    if path.is_absolute():
        return path
    return BASE_DIR / path


def format_resolution(width: int, height: int) -> str:
    return f"{width}x{height}"


def parse_bool(raw: str, default: bool = False) -> bool:
    """Parse common boolean strings from the environment."""
    value = raw.strip().lower()
    if not value:
        return default

    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False

    return default


def clamp_int(raw: str, default: int, minimum: int, maximum: int) -> int:
    """Clamp integer environment values to a safe range."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default

    return max(minimum, min(maximum, value))


def percent_to_native_scale(percent: int, maximum: int) -> int:
    """Convert a friendly 1-100 scale into a codec-native range like 0-9."""
    if maximum <= 0:
        return 0
    if percent <= 1:
        return 0

    # Ceil-style mapping keeps high values feeling meaningfully "high".
    return min(maximum, ((percent - 1) * maximum + 98) // 99)


def parse_padding_color(raw: str, has_alpha: bool) -> tuple[int, ...]:
    """Parse padding color from 'R,G,B[,A]' or 'transparent'."""
    value = raw.strip()

    if not value:
        return (255, 255, 255, 255) if has_alpha else (255, 255, 255)

    if value.lower() == "transparent":
        if has_alpha:
            return (255, 255, 255, 0)
        print("[WARN] PAD_COLOR=transparent requires an alpha-capable image. Using white.")
        return (255, 255, 255)

    parts = [part.strip() for part in value.split(",")]
    expected_lengths = {3, 4} if has_alpha else {3}
    fallback = (255, 255, 255, 255) if has_alpha else (255, 255, 255)

    if len(parts) not in expected_lengths:
        print(f"[WARN] Invalid PAD_COLOR '{raw}'. Using {fallback}.")
        return fallback

    try:
        channels = [max(0, min(255, int(part))) for part in parts]
    except ValueError:
        print(f"[WARN] Invalid PAD_COLOR '{raw}'. Using {fallback}.")
        return fallback

    if has_alpha and len(channels) == 3:
        channels.append(255)

    return tuple(channels)


def normalize_resize_mode(raw: str) -> str:
    """Map friendly RESIZE_MODE aliases to the internal mode names."""
    value = raw.strip().lower()
    aliases = {
        "cover": "cover",
        "crop": "cover",
        "cropping": "cover",
        "pad": "pad",
        "contain": "contain",
    }
    return aliases.get(value, value)


def parse_resolutions(raw: str) -> list[tuple[int, int]]:
    """Parse 'WxH,WxH,...' into a list of (width, height) tuples."""
    resolutions = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue

        try:
            width, height = part.lower().split("x")
            resolutions.append((int(width), int(height)))
        except ValueError:
            print(f"[WARN] Skipping invalid resolution: '{part}'")

    return resolutions


def parse_output_folder_labels(
    raw: str,
    resolutions: list[tuple[int, int]],
) -> list[str]:
    """
    Parse optional custom folder labels.

    When not set, folder labels default to the actual resize resolutions.
    """
    if not raw.strip():
        return [format_resolution(width, height) for width, height in resolutions]

    labels = [part.strip() for part in raw.split(",") if part.strip()]
    if len(labels) != len(resolutions):
        print("[ERROR] OUTPUT_FOLDER_LABELS count must match TARGET_RESOLUTIONS count.")
        print(f"        TARGET_RESOLUTIONS: {len(resolutions)}")
        print(f"        OUTPUT_FOLDER_LABELS: {len(labels)}")
        sys.exit(1)

    return labels


def get_crop_box(
    scaled_w: int,
    scaled_h: int,
    target_w: int,
    target_h: int,
    anchor: str,
) -> tuple[int, int, int, int]:
    """
    Return (left, upper, right, lower) crop box.
    Crops only the excess pixels; anchor controls which side is preserved.
    """
    excess_x = scaled_w - target_w
    excess_y = scaled_h - target_h

    anchor = anchor.lower()

    if anchor == "top":
        left = excess_x // 2
        upper = 0
    elif anchor == "bottom":
        left = excess_x // 2
        upper = excess_y
    elif anchor == "left":
        left = 0
        upper = excess_y // 2
    elif anchor == "right":
        left = excess_x
        upper = excess_y // 2
    else:
        left = excess_x // 2
        upper = excess_y // 2

    return (left, upper, left + target_w, upper + target_h)


def cover_resize(
    img: Image.Image,
    target_w: int,
    target_h: int,
    anchor: str = "center",
) -> Image.Image:
    """
    Scale image uniformly so it fully covers the target dimensions,
    then crop the minimal excess. No stretching, no letterboxing.
    """
    src_w, src_h = img.size

    scale_x = target_w / src_w
    scale_y = target_h / src_h
    scale = max(scale_x, scale_y)

    scaled_w = round(src_w * scale)
    scaled_h = round(src_h * scale)

    pixels_cropped_x = scaled_w - target_w
    pixels_cropped_y = scaled_h - target_h

    print(
        f"      scale={scale:.4f} -> {scaled_w}x{scaled_h}  "
        f"crop: {pixels_cropped_x}px horizontal, {pixels_cropped_y}px vertical"
    )

    resized = resize_image(img, (scaled_w, scaled_h))
    box = get_crop_box(scaled_w, scaled_h, target_w, target_h, anchor)
    return resized.crop(box)


def get_anchor_offsets(
    extra_w: int,
    extra_h: int,
    anchor: str,
) -> tuple[int, int]:
    """Return paste offsets for the resized image inside the target canvas."""
    anchor = anchor.lower()

    if anchor == "top":
        return (extra_w // 2, 0)
    if anchor == "bottom":
        return (extra_w // 2, extra_h)
    if anchor == "left":
        return (0, extra_h // 2)
    if anchor == "right":
        return (extra_w, extra_h // 2)

    return (extra_w // 2, extra_h // 2)


def contain_resize(
    img: Image.Image,
    target_w: int,
    target_h: int,
) -> Image.Image:
    """
    Scale image uniformly so the full content fits within the target.
    Output size may be smaller than the requested target.
    """
    src_w, src_h = img.size
    scale_x = target_w / src_w
    scale_y = target_h / src_h
    scale = min(scale_x, scale_y)

    scaled_w = round(src_w * scale)
    scaled_h = round(src_h * scale)

    print(f"      scale={scale:.4f} -> {scaled_w}x{scaled_h}  crop: 0px")
    return resize_image(img, (scaled_w, scaled_h))


def pad_resize(
    img: Image.Image,
    target_w: int,
    target_h: int,
    anchor: str = "center",
    pad_color: tuple[int, ...] = (255, 255, 255),
) -> Image.Image:
    """
    Scale image uniformly so the full content fits inside the target,
    then pad the remaining space instead of cropping.
    """
    contained = contain_resize(img, target_w, target_h)
    extra_w = target_w - contained.width
    extra_h = target_h - contained.height
    offset_x, offset_y = get_anchor_offsets(extra_w, extra_h, anchor)

    print(
        f"      pad : {extra_w}px horizontal, {extra_h}px vertical  "
        f"offset=({offset_x},{offset_y})"
    )

    if len(pad_color) == 4:
        canvas_mode = "RGBA"
    else:
        canvas_mode = "RGB"

    if contained.mode != canvas_mode:
        contained = contained.convert(canvas_mode)

    canvas = Image.new(canvas_mode, (target_w, target_h), pad_color)

    if "A" in contained.getbands():
        canvas.paste(contained, (offset_x, offset_y), contained.getchannel("A"))
    else:
        canvas.paste(contained, (offset_x, offset_y))

    return canvas


def normalize_image(original_img: Image.Image) -> Image.Image:
    """Apply EXIF orientation and preserve alpha when present."""
    img = ImageOps.exif_transpose(original_img)

    if "A" in img.getbands() or "transparency" in img.info:
        return img.convert("RGBA")

    return img.convert("RGB")


def has_transparency(img: Image.Image) -> bool:
    """Return True when the image contains any non-opaque pixels."""
    if "A" not in img.getbands():
        return "transparency" in img.info

    alpha_extrema = img.getchannel("A").getextrema()
    if not alpha_extrema:
        return False

    alpha_min, _ = alpha_extrema
    return alpha_min < 255


def resize_image(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """
    Resize with premultiplied alpha for transparent images to avoid edge halos.
    """
    if "A" in img.getbands():
        return img.convert("RGBa").resize(size, RESAMPLE_LANCZOS).convert("RGBA")

    return img.resize(size, RESAMPLE_LANCZOS)


def flatten_for_jpeg(img: Image.Image, background: tuple[int, int, int]) -> Image.Image:
    """JPEG does not support alpha, so flatten onto a solid background."""
    if "A" not in img.getbands():
        return img.convert("RGB")

    flattened = Image.new("RGB", img.size, background)
    flattened.paste(img, mask=img.getchannel("A"))
    return flattened


def resolve_output_format(output_format: str, source_ext: str) -> tuple[str | None, str]:
    """Resolve configured output format to Pillow format and file suffix."""
    normalized = output_format.strip().lower()

    if normalized in {"jpeg", "jpg"}:
        return ("JPEG", ".jpg")
    if normalized == "png":
        return ("PNG", ".png")
    if normalized == "webp":
        return ("WEBP", ".webp")
    if normalized == "original":
        return (None, source_ext)

    raise ValueError(
        "OUTPUT_FORMAT must be one of: original, jpeg, jpg, png, webp"
    )


def parse_jpeg_subsampling(raw: str) -> int | str:
    """Map friendly subsampling values to Pillow-compatible values."""
    value = raw.strip().lower()
    mapping = {
        "keep": "keep",
        "0": 0,
        "4:4:4": 0,
        "444": 0,
        "1": 1,
        "4:2:2": 1,
        "422": 1,
        "2": 2,
        "4:2:0": 2,
        "420": 2,
    }
    return mapping.get(value, 0)


def maybe_quantize_png(
    img: Image.Image,
    colors: int,
    use_dither: bool,
) -> Image.Image:
    """Optionally reduce PNG palette size for smaller files."""
    if colors <= 0:
        return img

    dither = DITHER_FLOYDSTEINBERG if use_dither else DITHER_NONE
    prepared = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    method = QUANTIZE_FASTOCTREE if "A" in prepared.getbands() else QUANTIZE_MEDIANCUT

    try:
        return prepared.quantize(colors=colors, method=method, dither=dither)
    except ValueError:
        print("      [WARN] PNG quantization failed; saving full-color PNG instead.")
        return img


def build_save_plan(
    result: Image.Image,
    source_ext: str,
    output_format: str,
    pad_color: tuple[int, ...],
    jpeg_quality: int,
    jpeg_subsampling: int | str,
    jpeg_progressive: bool,
    png_compress_level: int,
    png_quantize_colors: int,
    png_dither: bool,
    webp_quality: int,
    webp_method: int,
    webp_lossless: bool,
) -> tuple[Image.Image, str | None, str, dict]:
    """Prepare final image, extension, and save kwargs."""
    format_name, out_suffix = resolve_output_format(output_format, source_ext)
    target_format = format_name

    if target_format == "JPEG" or (target_format is None and source_ext in {".jpg", ".jpeg"}):
        if has_transparency(result):
            print(
                "      [INFO] JPEG does not support transparency; "
                f"flattening alpha onto {pad_color[:3]}."
            )
        prepared = flatten_for_jpeg(result, pad_color[:3]).convert("RGB")
        save_kwargs = {
            "quality": jpeg_quality,
            "optimize": True,
            "progressive": jpeg_progressive,
            "subsampling": jpeg_subsampling,
        }
        return (prepared, target_format, out_suffix, save_kwargs)

    if target_format == "PNG" or (target_format is None and source_ext == ".png"):
        prepared = maybe_quantize_png(result, png_quantize_colors, png_dither)
        save_kwargs = {
            "optimize": True,
            "compress_level": png_compress_level,
        }
        return (prepared, target_format, out_suffix, save_kwargs)

    if target_format == "WEBP" or (target_format is None and source_ext == ".webp"):
        save_kwargs = {
            "quality": webp_quality,
            "method": webp_method,
            "lossless": webp_lossless,
        }
        return (result, target_format, out_suffix, save_kwargs)

    return (result, target_format, out_suffix, {})


def main() -> None:
    env_path = load_environment()

    raw_resolutions = os.getenv("TARGET_RESOLUTIONS", "")
    raw_folder_labels = os.getenv("OUTPUT_FOLDER_LABELS", "")
    output_folder_prefix = os.getenv("OUTPUT_FOLDER_PREFIX", "")
    input_dir = resolve_config_path(os.getenv("INPUT_DIR", "input"), "input")
    output_dir = resolve_config_path(os.getenv("OUTPUT_DIR", "resize_output"), "resize_output")
    quality = clamp_int(os.getenv("QUALITY", "90"), 90, 1, 100)
    raw_resize_mode = os.getenv("RESIZE_MODE", "cover")
    resize_mode = normalize_resize_mode(raw_resize_mode)
    output_format = os.getenv("OUTPUT_FORMAT", "original").strip().lower()
    anchor = os.getenv("CROP_ANCHOR", "center")
    raw_pad_color = os.getenv("PAD_COLOR", "")
    jpeg_quality = quality
    jpeg_progressive = parse_bool(os.getenv("JPEG_PROGRESSIVE", "true"), True)
    jpeg_subsampling = parse_jpeg_subsampling(os.getenv("JPEG_SUBSAMPLING", "4:4:4"))
    png_compression = quality
    png_compress_level = percent_to_native_scale(png_compression, 9)
    png_quantize_colors = clamp_int(os.getenv("PNG_QUANTIZE_COLORS", "0"), 0, 0, 256)
    png_dither = parse_bool(os.getenv("PNG_DITHER", "false"), False)
    webp_quality = quality
    webp_effort = quality
    webp_method = percent_to_native_scale(webp_effort, 6)
    webp_lossless = parse_bool(os.getenv("WEBP_LOSSLESS", "false"), False)

    valid_modes = {"cover", "pad", "contain"}
    if resize_mode not in valid_modes:
        print("[ERROR] RESIZE_MODE must be one of: contain, cover, crop, cropping, pad")
        sys.exit(1)

    try:
        resolve_output_format(output_format, ".png")
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    if not raw_resolutions:
        expected_env = env_path if env_path else BASE_DIR / ".env"
        print(f"[ERROR] TARGET_RESOLUTIONS is not set in {expected_env}")
        sys.exit(1)

    resolutions = parse_resolutions(raw_resolutions)
    if not resolutions:
        print("[ERROR] No valid resolutions found in TARGET_RESOLUTIONS")
        sys.exit(1)

    folder_labels = parse_output_folder_labels(raw_folder_labels, resolutions)

    if not input_dir.exists():
        print(f"[ERROR] Input folder not found: {input_dir.resolve()}")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    images = [
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_FORMATS
    ]

    if not images:
        print(f"[WARN] No supported images found in '{input_dir}'")
        sys.exit(0)

    mode_label = resize_mode
    if raw_resize_mode.strip().lower() != resize_mode:
        mode_label = f"{raw_resize_mode.strip().lower()} -> {resize_mode}"

    print(f"\n{SEPARATOR}")
    print(f"  Config : {env_path if env_path else 'default environment'}")
    print(f"  Input  : {input_dir.resolve()}")
    print(f"  Output : {output_dir.resolve()}")
    print(f"  Mode   : {mode_label}   Anchor: {anchor}")
    print(f"  Format : {output_format}")
    print(f"  Quality: {quality}/100")
    print(
        f"  JPEG   : quality={jpeg_quality}/100 progressive={jpeg_progressive} "
        f"subsampling={jpeg_subsampling}"
    )
    print(
        f"  PNG    : compression={png_compression}/100 "
        f"(level {png_compress_level}/9) "
        f"quantize_colors={png_quantize_colors} dither={png_dither}"
    )
    print(
        f"  WEBP   : quality={webp_quality}/100 "
        f"effort={webp_effort}/100 (method {webp_method}/6) "
        f"lossless={webp_lossless}"
    )
    print("  Target resolutions:")
    for (target_w, target_h), folder_label in zip(resolutions, folder_labels):
        folder_name = f"{output_folder_prefix}{folder_label}"
        print(
            f"    - {target_w}x{target_h}  "
            f"(folder: {folder_name}, aspect {target_w / target_h:.4f})"
        )
    print(f"{SEPARATOR}\n")

    total = 0

    for img_path in sorted(images):
        print(f"[IMAGE] {img_path.name}")

        try:
            with Image.open(img_path) as original_img:
                img = normalize_image(original_img)
                pad_color = parse_padding_color(raw_pad_color, "A" in img.getbands())
                src_w, src_h = img.size
                print(f"    Source: {src_w}x{src_h}  (aspect {src_w / src_h:.4f})")

                for (target_w, target_h), folder_label in zip(resolutions, folder_labels):
                    print(f"    -> {target_w}x{target_h}")

                    if resize_mode == "cover":
                        result = cover_resize(img, target_w, target_h, anchor)
                    elif resize_mode == "contain":
                        result = contain_resize(img, target_w, target_h)
                    else:
                        result = pad_resize(img, target_w, target_h, anchor, pad_color)

                    folder_name = f"{output_folder_prefix}{folder_label}"
                    res_folder = output_dir / folder_name
                    res_folder.mkdir(parents=True, exist_ok=True)
                    ext = img_path.suffix.lower()
                    prepared, format_name, out_suffix, save_kwargs = build_save_plan(
                        result=result,
                        source_ext=ext,
                        output_format=output_format,
                        pad_color=pad_color,
                        jpeg_quality=jpeg_quality,
                        jpeg_subsampling=jpeg_subsampling,
                        jpeg_progressive=jpeg_progressive,
                        png_compress_level=png_compress_level,
                        png_quantize_colors=png_quantize_colors,
                        png_dither=png_dither,
                        webp_quality=webp_quality,
                        webp_method=webp_method,
                        webp_lossless=webp_lossless,
                    )
                    out_path = res_folder / f"{img_path.stem}{out_suffix}"

                    if format_name:
                        prepared.save(out_path, format=format_name, **save_kwargs)
                    else:
                        prepared.save(out_path, **save_kwargs)
                    print(f"      [OK] Saved -> {out_path}")
                    total += 1

        except Exception as exc:
            print(f"    [ERROR] Error processing {img_path.name}: {exc}")

        print()

    print(SEPARATOR)
    print(f"[OK] Done. {total} image(s) exported to '{output_dir.resolve()}'")
    print(SEPARATOR)


if __name__ == "__main__":
    main()
