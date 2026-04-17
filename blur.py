"""
Interactive Image Blur Tool
===========================
- Works with a single image or a folder of images
- Supports multiple blur modes
- Can export multiple blur strengths in one run
- Uses the resize targets and folder naming from .env when available
- Preserves transparent edges with premultiplied-alpha blurring

Install dependencies:
    pip install Pillow

Usage:
    python blur.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps
from resize import (
    contain_resize,
    cover_resize,
    load_environment,
    normalize_resize_mode,
    pad_resize,
    parse_output_folder_labels,
    parse_padding_color,
    parse_resolutions,
)

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = BASE_DIR / "input"
DEFAULT_OUTPUT_DIR = BASE_DIR / "blur_output"
SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
SEPARATOR = "-" * 60

BLUR_MODE_LABELS = {
    "1": ("gaussian", "Gaussian blur"),
    "2": ("box", "Box blur"),
    "3": ("median", "Median filter"),
    "4": ("simple", "Simple blur repeat"),
}


def prompt_text(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""

    while True:
        raw = input(f"{prompt}{suffix}: ").strip()
        if raw:
            return raw
        if default is not None:
            return default
        print("Please enter a value.")


def prompt_yes_no(prompt: str, default: bool = False) -> bool:
    default_hint = "Y/n" if default else "y/N"

    while True:
        raw = input(f"{prompt} [{default_hint}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Please answer with yes or no.")


def prompt_blur_mode() -> str:
    print("Choose a blur mode:")
    for key, (_, label) in BLUR_MODE_LABELS.items():
        print(f"  {key}. {label}")

    while True:
        choice = input("Blur mode [1]: ").strip() or "1"
        if choice in BLUR_MODE_LABELS:
            return BLUR_MODE_LABELS[choice][0]
        print("Please choose 1, 2, 3, or 4.")


def resolve_user_path(raw_path: str, default_path: Path) -> Path:
    value = raw_path.strip()
    if not value:
        return default_path

    path = Path(value)
    if path.is_absolute():
        return path
    return BASE_DIR / path


def parse_float_values(raw: str) -> list[float]:
    values = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue

        try:
            value = float(part)
        except ValueError as exc:
            raise ValueError(f"'{part}' is not a number.") from exc

        if value < 0:
            raise ValueError("Blur radius cannot be negative.")
        values.append(value)

    if not values:
        raise ValueError("Enter at least one blur value.")

    return values


def parse_positive_int_values(raw: str) -> list[int]:
    values = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue

        try:
            value = int(part)
        except ValueError as exc:
            raise ValueError(f"'{part}' is not a whole number.") from exc

        if value <= 0:
            raise ValueError("Values must be greater than zero.")
        values.append(value)

    if not values:
        raise ValueError("Enter at least one value.")

    return values


def parse_odd_int_values(raw: str) -> list[int]:
    values = parse_positive_int_values(raw)
    for value in values:
        if value < 3 or value % 2 == 0:
            raise ValueError("Median filter size must be odd and at least 3.")
    return values


def prompt_settings(mode: str) -> list[int | float]:
    prompts = {
        "gaussian": "Gaussian radius values (comma separated, example: 1,2.5,5)",
        "box": "Box blur radius values (comma separated, example: 1,3,8)",
        "median": "Median filter sizes (odd numbers, example: 3,5,7)",
        "simple": "Simple blur repeat counts (whole numbers, example: 1,2,4)",
    }

    parsers = {
        "gaussian": parse_float_values,
        "box": parse_float_values,
        "median": parse_odd_int_values,
        "simple": parse_positive_int_values,
    }

    while True:
        raw = input(f"{prompts[mode]}: ").strip()
        try:
            return parsers[mode](raw)
        except ValueError as exc:
            print(f"Invalid input: {exc}")


def normalize_image(original_img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(original_img)
    if "A" in img.getbands() or "transparency" in img.info:
        return img.convert("RGBA")
    if img.mode == "L":
        return img
    return img.convert("RGB")


def has_transparency(img: Image.Image) -> bool:
    if "A" not in img.getbands():
        return "transparency" in img.info

    alpha_extrema = img.getchannel("A").getextrema()
    if not alpha_extrema:
        return False

    alpha_min, _ = alpha_extrema
    return alpha_min < 255


def flatten_for_jpeg(img: Image.Image) -> Image.Image:
    if "A" not in img.getbands():
        return img.convert("RGB")

    flattened = Image.new("RGB", img.size, (255, 255, 255))
    flattened.paste(img, mask=img.getchannel("A"))
    return flattened


def format_setting_value(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"


def format_setting_slug(value: int | float) -> str:
    return format_setting_value(value).replace(".", "_")


def is_within(path: Path, possible_parent: Path) -> bool:
    try:
        path.resolve().relative_to(possible_parent.resolve())
        return True
    except ValueError:
        return False


def collect_images(source_path: Path, output_path: Path) -> list[Path]:
    if source_path.is_file():
        if source_path.suffix.lower() not in SUPPORTED_FORMATS:
            print(f"[ERROR] Unsupported file type: {source_path.name}")
            sys.exit(1)
        return [source_path]

    if not source_path.is_dir():
        print(f"[ERROR] Source path not found: {source_path.resolve()}")
        sys.exit(1)

    images = []
    for path in sorted(source_path.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_FORMATS:
            continue
        if output_path.exists() and is_within(path, output_path):
            continue
        images.append(path)

    if not images:
        print(f"[WARN] No supported images found in {source_path.resolve()}")
        sys.exit(0)

    return images


def load_target_configuration() -> tuple[Path | None, list[tuple[int, int, str]], str, str]:
    """Load optional target resolutions and folder naming from .env."""
    env_path = load_environment()
    raw_resolutions = os.getenv("TARGET_RESOLUTIONS", "")
    raw_folder_labels = os.getenv("OUTPUT_FOLDER_LABELS", "")
    output_folder_prefix = os.getenv("OUTPUT_FOLDER_PREFIX", "")
    resize_mode = normalize_resize_mode(os.getenv("RESIZE_MODE", "cover"))
    anchor = os.getenv("CROP_ANCHOR", "center")

    if not raw_resolutions.strip():
        return (env_path, [], resize_mode, anchor)

    resolutions = parse_resolutions(raw_resolutions)
    if not resolutions:
        print("[WARN] TARGET_RESOLUTIONS is set, but no valid resolutions were found.")
        return (env_path, [], resize_mode, anchor)

    folder_labels = parse_output_folder_labels(raw_folder_labels, resolutions)
    targets = [
        (target_w, target_h, f"{output_folder_prefix}{folder_label}")
        for (target_w, target_h), folder_label in zip(resolutions, folder_labels)
    ]
    return (env_path, targets, resize_mode, anchor)


def resize_for_target(
    img: Image.Image,
    target_w: int,
    target_h: int,
    resize_mode: str,
    anchor: str,
    pad_color: tuple[int, ...],
) -> Image.Image:
    """Resize an image to a target resolution before applying blur."""
    if resize_mode == "cover":
        return cover_resize(img, target_w, target_h, anchor)
    if resize_mode == "contain":
        return contain_resize(img, target_w, target_h)
    return pad_resize(img, target_w, target_h, anchor, pad_color)


def blur_single_channel(img: Image.Image, mode: str, value: int | float) -> Image.Image:
    if mode == "gaussian":
        return img.filter(ImageFilter.GaussianBlur(radius=float(value)))
    if mode == "box":
        return img.filter(ImageFilter.BoxBlur(radius=float(value)))
    if mode == "median":
        return img.filter(ImageFilter.MedianFilter(size=int(value)))
    if mode == "simple":
        result = img
        for _ in range(int(value)):
            result = result.filter(ImageFilter.BLUR)
        return result

    raise ValueError(f"Unsupported blur mode: {mode}")


def apply_blur(img: Image.Image, mode: str, value: int | float) -> Image.Image:
    if "A" in img.getbands():
        premultiplied = img.convert("RGBa")
        blurred_channels = [
            blur_single_channel(channel, mode, value)
            for channel in premultiplied.split()
        ]
        return Image.merge("RGBa", blurred_channels).convert("RGBA")

    if img.mode == "L":
        return blur_single_channel(img, mode, value)

    return blur_single_channel(img.convert("RGB"), mode, value)


def build_output_path(
    img_path: Path,
    source_path: Path,
    output_path: Path,
    mode: str,
    value: int | float,
    folder_name: str | None = None,
) -> Path:
    setting_folder = output_path / f"{mode}-{format_setting_slug(value)}"
    if folder_name:
        setting_folder = setting_folder / folder_name

    if source_path.is_file():
        relative_path = Path(img_path.name)
    else:
        relative_path = img_path.relative_to(source_path)

    return setting_folder / relative_path


def save_result(img: Image.Image, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    suffix = out_path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        if has_transparency(img):
            print("  [INFO] JPEG does not support transparency; flattening alpha onto white.")
        flatten_for_jpeg(img).save(out_path, quality=92, optimize=True)
        return

    img.save(out_path)


def main() -> None:
    print(SEPARATOR)
    print("Interactive Image Blur Tool")
    print(SEPARATOR)

    env_path, targets, resize_mode, anchor = load_target_configuration()
    source_raw = prompt_text("Source image or folder", str(DEFAULT_INPUT_DIR))
    output_raw = prompt_text("Output folder", str(DEFAULT_OUTPUT_DIR))
    mode = prompt_blur_mode()
    settings = prompt_settings(mode)
    overwrite = prompt_yes_no("Overwrite existing files", False)

    source_path = resolve_user_path(source_raw, DEFAULT_INPUT_DIR)
    output_path = resolve_user_path(output_raw, DEFAULT_OUTPUT_DIR)
    images = collect_images(source_path, output_path)

    print(f"\n{SEPARATOR}")
    print(f"Env       : {env_path if env_path else 'default environment'}")
    print(f"Source    : {source_path.resolve()}")
    print(f"Output    : {output_path.resolve()}")
    if targets:
        print(f"Targets   : {len(targets)} from .env  Resize: {resize_mode}  Anchor: {anchor}")
    else:
        print("Targets   : original size (TARGET_RESOLUTIONS not set)")
    print(f"Blur mode : {mode}")
    print(f"Settings  : {', '.join(format_setting_value(value) for value in settings)}")
    print(f"Images    : {len(images)}")
    print(f"Overwrite : {overwrite}")
    print(f"{SEPARATOR}\n")

    saved = 0
    skipped = 0

    for img_path in images:
        print(f"[IMAGE] {img_path}")

        try:
            with Image.open(img_path) as original_img:
                img = normalize_image(original_img)
                pad_color = parse_padding_color(os.getenv("PAD_COLOR", ""), "A" in img.getbands())

                variants: list[tuple[Image.Image, str | None]] = []
                if targets:
                    for target_w, target_h, folder_name in targets:
                        print(f"  [TARGET] {folder_name} -> {target_w}x{target_h}")
                        resized = resize_for_target(
                            img=img,
                            target_w=target_w,
                            target_h=target_h,
                            resize_mode=resize_mode,
                            anchor=anchor,
                            pad_color=pad_color,
                        )
                        variants.append((resized, folder_name))
                else:
                    variants.append((img, None))

                for value in settings:
                    for variant_img, folder_name in variants:
                        result = apply_blur(variant_img, mode, value)
                        out_path = build_output_path(
                            img_path=img_path,
                            source_path=source_path,
                            output_path=output_path,
                            mode=mode,
                            value=value,
                            folder_name=folder_name,
                        )

                        if out_path.exists() and not overwrite:
                            print(f"  [SKIP] {mode}-{format_setting_value(value)} -> {out_path}")
                            skipped += 1
                            continue

                        save_result(result, out_path)
                        print(f"  [OK]   {mode}-{format_setting_value(value)} -> {out_path}")
                        saved += 1

        except Exception as exc:
            print(f"  [ERROR] {img_path.name}: {exc}")

        print()

    print(SEPARATOR)
    print(f"[OK] Done. Saved {saved} file(s), skipped {skipped}.")
    print(SEPARATOR)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Cancelled by user.")
