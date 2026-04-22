from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv
from PIL import Image, ImageFilter, ImageOps

BASE_DIR = Path(__file__).resolve().parent
ENV_CANDIDATES = (BASE_DIR / ".env", BASE_DIR / "env")
SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
SEPARATOR = "-" * 60

BLUR_MODE_LABELS = {
    "gaussian": "Gaussian blur - smooth natural-looking blur",
    "box": "Box blur - even blur across the whole image",
    "median": "Median filter - reduces noise while keeping edges cleaner",
    "simple": "Simple blur repeat - stacks the basic blur filter multiple times",
}

FORMAT_SUFFIXES = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "TIFF": ".tiff",
}
SUFFIX_TO_FORMAT = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
    ".bmp": "BMP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}


@dataclass
class JobSummary:
    saved: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass(frozen=True)
class BlurJob:
    env_path: Path | None
    source_path: Path
    output_dir: Path
    blur_mode: str
    blur_values: list[int | float]
    pad_color_raw: str = ""
    output_format: str = "original"
    overwrite: bool = False


def load_environment() -> Path | None:
    for candidate in ENV_CANDIDATES:
        if candidate.exists():
            load_dotenv(candidate)
            return candidate

    load_dotenv()
    return None


def resolve_config_path(raw_path: str, default: str) -> Path:
    path = Path(raw_path or default)
    if path.is_absolute():
        return path
    return BASE_DIR / path


def normalize_source_format(source_format: str | None) -> str | None:
    if not source_format:
        return None

    normalized = source_format.strip().upper()
    if normalized == "JPG":
        return "JPEG"
    return normalized


def canonical_suffix_for_format(
    source_format: str | None,
    fallback_suffix: str,
) -> str:
    normalized = normalize_source_format(source_format)
    if normalized == "TIFF" and fallback_suffix.lower() in {".tif", ".tiff"}:
        return fallback_suffix.lower()
    return FORMAT_SUFFIXES.get(normalized, fallback_suffix.lower())


def parse_float_values(raw: str) -> list[float]:
    values: list[float] = []
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
    values: list[int] = []
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


def parse_blur_values(mode: str, raw: str) -> list[int | float]:
    normalized = mode.strip().lower()
    parsers = {
        "gaussian": parse_float_values,
        "box": parse_float_values,
        "median": parse_odd_int_values,
        "simple": parse_positive_int_values,
    }
    if normalized not in parsers:
        raise ValueError("Blur mode must be one of: gaussian, box, median, simple")
    return parsers[normalized](raw)


def load_blur_job() -> BlurJob:
    env_path = load_environment()
    blur_mode = os.getenv("BLUR_MODE", "gaussian").strip().lower() or "gaussian"
    blur_values = parse_blur_values(blur_mode, os.getenv("BLUR_VALUES", "2"))
    return BlurJob(
        env_path=env_path,
        source_path=resolve_config_path(os.getenv("INPUT_DIR", "input"), "input"),
        output_dir=resolve_config_path(os.getenv("BLUR_OUTPUT_DIR", "blur_output"), "blur_output"),
        blur_mode=blur_mode,
        blur_values=blur_values,
        pad_color_raw=os.getenv("PAD_COLOR", ""),
        output_format=os.getenv("OUTPUT_FORMAT", "original").strip().lower() or "original",
        overwrite=False,
    )


def normalize_image(original_img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(original_img)
    if "A" in img.getbands() or "transparency" in img.info:
        return img.convert("RGBA")
    if img.mode == "L":
        return img
    return img.convert("RGB")


def flatten_for_jpeg(
    img: Image.Image,
    background: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    if "A" not in img.getbands():
        return img.convert("RGB")

    flattened = Image.new("RGB", img.size, background)
    flattened.paste(img, mask=img.getchannel("A"))
    return flattened


def parse_padding_color(raw: str, has_alpha: bool) -> tuple[int, ...]:
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


def has_transparency(img: Image.Image) -> bool:
    if "A" not in img.getbands():
        return "transparency" in img.info

    alpha_extrema = img.getchannel("A").getextrema()
    if not alpha_extrema:
        return False

    alpha_min, _ = alpha_extrema
    return alpha_min < 255


def resolve_original_save_target(
    source_ext: str,
    source_format: str | None,
) -> tuple[str | None, str]:
    normalized = normalize_source_format(source_format)
    if normalized:
        return normalized, source_ext.lower()
    return SUFFIX_TO_FORMAT.get(source_ext.lower()), source_ext.lower()


def resolve_output_format(
    output_format: str,
    source_ext: str,
    source_format: str | None = None,
    prefer_png_for_transparency: bool = False,
) -> tuple[str | None, str]:
    normalized = output_format.strip().lower()

    if normalized == "auto":
        if prefer_png_for_transparency:
            return "PNG", ".png"
        return "JPEG", ".jpg"
    if normalized in {"jpeg", "jpg"}:
        return "JPEG", ".jpg"
    if normalized == "png":
        return "PNG", ".png"
    if normalized == "webp":
        return "WEBP", ".webp"
    if normalized == "original":
        return resolve_original_save_target(source_ext, source_format)

    raise ValueError("OUTPUT_FORMAT must be one of: original, auto, jpeg, jpg, png, webp")


def prepare_save(
    result: Image.Image,
    source_ext: str,
    source_format: str | None,
    output_format: str,
    pad_color: tuple[int, ...],
) -> tuple[Image.Image, str | None, str]:
    format_name, suffix = resolve_output_format(
        output_format,
        source_ext,
        source_format,
        prefer_png_for_transparency=has_transparency(result),
    )
    if format_name == "JPEG":
        return flatten_for_jpeg(result, pad_color[:3]).convert("RGB"), format_name, suffix
    return result, format_name, suffix


def is_within(path: Path, possible_parent: Path) -> bool:
    try:
        path.resolve().relative_to(possible_parent.resolve())
        return True
    except ValueError:
        return False


def collect_images(
    source_path: Path,
    exclude_roots: Sequence[Path] | None = None,
) -> list[Path]:
    if source_path.is_file():
        if source_path.suffix.lower() not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported file type: {source_path.name}")
        return [source_path]

    if not source_path.is_dir():
        raise ValueError(f"Source path not found: {source_path.resolve()}")

    images: list[Path] = []
    excluded = [path for path in (exclude_roots or []) if path.exists()]
    for path in sorted(source_path.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_FORMATS:
            continue
        if any(is_within(path, root) for root in excluded):
            continue
        images.append(path)

    return images


def build_output_path(
    source_root: Path,
    img_path: Path,
    output_root: Path,
    suffix: str | None = None,
    stem_suffix: str = "",
) -> Path:
    if source_root.is_file():
        relative_path = Path(img_path.name)
    else:
        relative_path = img_path.relative_to(source_root)

    target_name = relative_path.name
    if stem_suffix:
        target_name = f"{relative_path.stem}{stem_suffix}{relative_path.suffix}"
        relative_path = relative_path.with_name(target_name)
    if suffix is not None:
        relative_path = relative_path.with_suffix(suffix)

    return output_root / relative_path


def build_unique_output_path(
    source_root: Path,
    img_path: Path,
    output_root: Path,
    suffix: str | None = None,
    stem_suffix: str = "",
    reserved_paths: set[str] | None = None,
) -> tuple[Path, bool]:
    base_path = build_output_path(
        source_root=source_root,
        img_path=img_path,
        output_root=output_root,
        suffix=suffix,
        stem_suffix=stem_suffix,
    )
    if reserved_paths is None:
        return base_path, False

    def reserve(path: Path) -> bool:
        key = str(path).lower()
        if key in reserved_paths:
            return False
        reserved_paths.add(key)
        return True

    if reserve(base_path):
        return base_path, False

    source_tag = img_path.suffix.lower().lstrip(".") or "file"
    tagged_stem_suffix = f"{stem_suffix}__from_{source_tag}"

    attempt = 1
    while True:
        candidate_stem_suffix = tagged_stem_suffix
        if attempt > 1:
            candidate_stem_suffix = f"{tagged_stem_suffix}__{attempt}"

        candidate = build_output_path(
            source_root=source_root,
            img_path=img_path,
            output_root=output_root,
            suffix=suffix,
            stem_suffix=candidate_stem_suffix,
        )
        if reserve(candidate):
            return candidate, True
        attempt += 1


def save_image_to_path(image: Image.Image, out_path: Path, format_name: str | None) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if format_name:
        image.save(out_path, format=format_name)
    else:
        image.save(out_path)


def print_prompt_help(help_text: str | None = None) -> None:
    if not help_text:
        return

    for line in help_text.splitlines():
        print(f"  {line}")


def prompt_text(prompt: str, default: str | None = None, help_text: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    print_prompt_help(help_text)

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


def prompt_choice(
    title: str,
    options: dict[str, str],
    default: str,
    input_label: str = "Choose",
) -> str:
    print(title)
    for key, label in options.items():
        lines = label.splitlines() or [label]
        print(f"  {key}. {lines[0]}")
        for line in lines[1:]:
            print(f"     {line}")

    while True:
        choice = input(f"{input_label} [{default}]: ").strip().lower() or default
        if choice in options:
            return choice
        print(f"Please choose one of: {', '.join(options)}")


def resolve_user_path(raw_path: str, default_path: Path, base_dir: Path = BASE_DIR) -> Path:
    value = raw_path.strip()
    if not value:
        return default_path

    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path


def prompt_path(prompt: str, default_path: Path, base_dir: Path = BASE_DIR) -> Path:
    raw = prompt_text(prompt, str(default_path))
    return resolve_user_path(raw, default_path, base_dir)


def prompt_blur_mode(default: str = "gaussian") -> str:
    values = {str(index): mode for index, mode in enumerate(BLUR_MODE_LABELS, start=1)}
    labels = {key: BLUR_MODE_LABELS[mode] for key, mode in values.items()}
    default_key = next((key for key, value in values.items() if value == default), "1")
    choice = prompt_choice(
        "Choose a blur mode:",
        labels,
        default_key,
        input_label="Blur mode",
    )
    return values[choice]


def prompt_output_format(default: str) -> str:
    values = {
        "1": "original",
        "2": "auto",
        "3": "jpeg",
        "4": "png",
        "5": "webp",
    }
    labels = {
        "1": "original - keep the source format and extension",
        "2": "auto     - Autodetect transparency and use png, otherwise JPEG",
        "3": "jpeg     - force JPEG output",
        "4": "png      - force PNG output",
        "5": "webp     - force WebP output",
    }
    default_key = next((key for key, value in values.items() if value == default), "1")
    choice = prompt_choice(
        "Choose output image format / extension:",
        labels,
        default_key,
        input_label="Output format",
    )
    return values[choice]


def format_blur_values(values: list[int | float]) -> str:
    return ",".join(format_setting_value(value) for value in values)


def prompt_blur_values(mode: str, default_values: list[int | float] | None = None) -> list[int | float]:
    prompts = {
        "gaussian": "Gaussian radius values (example: 1,2.5,5)",
        "box": "Box blur radius values (example: 1,3,8)",
        "median": "Median filter sizes (odd numbers, example: 3,5,7)",
        "simple": "Simple blur repeat counts (whole numbers, example: 1,2,4)",
    }
    default_values_by_mode = {
        "gaussian": "2",
        "box": "2",
        "median": "3",
        "simple": "1",
    }
    help_text = "Tip: enter one value for one output, or use commas for multiple outputs."
    default_raw = (
        format_blur_values(default_values)
        if default_values
        else default_values_by_mode[mode]
    )

    while True:
        raw = prompt_text(prompts[mode], default_raw, help_text=help_text)
        try:
            return parse_blur_values(mode, raw)
        except ValueError as exc:
            print(f"Invalid input: {exc}")


def prompt_blur_job(defaults: BlurJob) -> BlurJob:
    source_path = prompt_path("Source image or folder", defaults.source_path)
    output_dir = prompt_path("Output folder", defaults.output_dir)
    blur_mode = prompt_blur_mode(defaults.blur_mode)
    default_values = defaults.blur_values if blur_mode == defaults.blur_mode else None
    blur_values = prompt_blur_values(blur_mode, default_values)
    output_format = prompt_output_format(defaults.output_format)
    overwrite = prompt_yes_no("Overwrite existing files", defaults.overwrite)

    return BlurJob(
        env_path=defaults.env_path,
        source_path=source_path,
        output_dir=output_dir,
        blur_mode=blur_mode,
        blur_values=blur_values,
        pad_color_raw=defaults.pad_color_raw,
        output_format=output_format,
        overwrite=overwrite,
    )


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


def format_setting_value(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"


def format_setting_slug(value: int | float) -> str:
    return format_setting_value(value).replace(".", "_")


def validate_blur_job(job: BlurJob) -> None:
    if job.blur_mode not in BLUR_MODE_LABELS:
        raise ValueError("Blur mode must be one of: gaussian, box, median, simple")
    if not job.blur_values:
        raise ValueError("Enter at least one blur value.")
    resolve_output_format(job.output_format, ".png")


def run_blur(job: BlurJob) -> JobSummary:
    validate_blur_job(job)
    images = collect_images(job.source_path, exclude_roots=[job.output_dir])

    if not images:
        print(f"[WARN] No supported images found in '{job.source_path.resolve()}'")
        return JobSummary()

    summary = JobSummary()
    reserved_outputs: set[str] = set()

    print(f"\n{SEPARATOR}")
    print("Blur Images")
    print(f"{SEPARATOR}")
    print(f"  Config : {job.env_path if job.env_path else 'default environment'}")
    print(f"  Source : {job.source_path.resolve()}")
    print(f"  Output : {job.output_dir.resolve()}")
    print(f"  Blur   : {job.blur_mode}")
    print(f"  Values : {', '.join(format_setting_value(value) for value in job.blur_values)}")
    print(f"  Format : {job.output_format}")
    print(f"  Images : {len(images)}")
    print(f"  Overwrite: {job.overwrite}")
    print(f"{SEPARATOR}\n")

    for img_path in images:
        label = img_path.name if job.source_path.is_file() else img_path.relative_to(job.source_path)
        print(f"[IMAGE] {label}")

        try:
            with Image.open(img_path) as original_img:
                source_ext = img_path.suffix.lower()
                source_format = normalize_source_format(original_img.format)
                canonical_suffix = canonical_suffix_for_format(source_format, source_ext)
                img = normalize_image(original_img)
                pad_color = parse_padding_color(job.pad_color_raw, "A" in img.getbands())

                if source_format and canonical_suffix != source_ext and job.output_format == "original":
                    print(
                        f"  [INFO] File extension {source_ext} contains {source_format} data. "
                        "Blur keeps the source filename unchanged."
                    )

                for value in job.blur_values:
                    result = apply_blur(img, job.blur_mode, value)
                    prepared, format_name, suffix = prepare_save(
                        result=result,
                        source_ext=source_ext,
                        source_format=source_format,
                        output_format=job.output_format,
                        pad_color=pad_color,
                    )
                    out_path, renamed_for_collision = build_unique_output_path(
                        source_root=job.source_path,
                        img_path=img_path,
                        output_root=job.output_dir / f"{job.blur_mode}-{format_setting_slug(value)}",
                        suffix=suffix,
                        reserved_paths=reserved_outputs,
                    )
                    if renamed_for_collision:
                        print(
                            "  [INFO] Adjusted output name to avoid overwriting another source file: "
                            f"{out_path.name}"
                        )

                    if out_path.exists() and not job.overwrite:
                        print(f"  [SKIP] {job.blur_mode}-{format_setting_value(value)} -> {out_path}")
                        summary.skipped += 1
                        continue

                    save_image_to_path(prepared, out_path, format_name)
                    print(f"  [OK]   {job.blur_mode}-{format_setting_value(value)} -> {out_path}")
                    summary.saved += 1

        except Exception as exc:
            print(f"  [ERROR] {img_path.name}: {exc}")
            summary.failed += 1

        print()

    print(SEPARATOR)
    print(
        f"[OK] Done. Saved {summary.saved} file(s), "
        f"skipped {summary.skipped}, failed {summary.failed}."
    )
    print(SEPARATOR)
    return summary


def main() -> None:
    defaults = load_blur_job()
    print(SEPARATOR)
    print("Interactive Blur Tool")
    print(SEPARATOR)
    run_blur(prompt_blur_job(defaults))


__all__ = [
    "BASE_DIR",
    "BLUR_MODE_LABELS",
    "BlurJob",
    "JobSummary",
    "SEPARATOR",
    "format_setting_slug",
    "format_setting_value",
    "load_blur_job",
    "parse_blur_values",
    "parse_float_values",
    "parse_odd_int_values",
    "parse_positive_int_values",
    "prompt_blur_job",
    "resolve_output_format",
    "run_blur",
]


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Cancelled by user.")
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)
