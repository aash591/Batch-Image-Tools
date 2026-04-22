from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv
from PIL import Image, ImageOps

BASE_DIR = Path(__file__).resolve().parent
ENV_CANDIDATES = (BASE_DIR / ".env", BASE_DIR / "env")
SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
SEPARATOR = "-" * 60

FORMAT_SUFFIXES = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "TIFF": ".tiff",
}

AUTO_PNG_MIN_TRANSPARENT_RATIO = 0.02
AUTO_PNG_BORDER_TOLERANCE = 16

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


@dataclass
class JobSummary:
    saved: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass
class CompressionSummary(JobSummary):
    input_bytes: int = 0
    output_bytes: int = 0


@dataclass(frozen=True)
class CompressJob:
    env_path: Path | None
    input_dir: Path
    output_dir: Path
    compression_value: int
    output_format: str
    pad_color_raw: str
    jpeg_quality: int
    jpeg_progressive: bool
    jpeg_subsampling: int | str
    png_compress_level: int
    png_quantize_colors: int
    png_dither: bool
    webp_quality: int
    webp_method: int
    webp_lossless: bool
    overwrite: bool = True


@dataclass(frozen=True)
class SavePlan:
    image: Image.Image
    format_name: str | None
    suffix: str
    save_kwargs: dict[str, object]


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


def parse_bool(raw: str, default: bool = False) -> bool:
    value = raw.strip().lower()
    if not value:
        return default

    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def clamp_int(raw: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default

    return max(minimum, min(maximum, value))


def get_compression_setting(default: int = 90) -> int:
    raw_compression = os.getenv("COMPRESSION", "").strip()
    if raw_compression:
        return clamp_int(raw_compression, default, 1, 100)

    raw_quality = os.getenv("QUALITY", "").strip()
    if raw_quality:
        print("[INFO] QUALITY is deprecated. Use COMPRESSION instead.")
        return clamp_int(raw_quality, default, 1, 100)

    return default


def percent_to_native_scale(percent: int, maximum: int) -> int:
    if maximum <= 0:
        return 0
    if percent <= 1:
        return 0
    return min(maximum, ((percent - 1) * maximum + 98) // 99)


def compression_to_quality(compression: int) -> int:
    bounded = max(1, min(100, compression))
    return 101 - bounded


def parse_jpeg_subsampling(raw: str) -> int | str:
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


def has_meaningful_transparency(img: Image.Image) -> bool:
    if "A" not in img.getbands():
        return "transparency" in img.info

    alpha = img.getchannel("A")
    histogram = alpha.histogram()
    transparent_pixels = histogram[0]
    partial_pixels = sum(histogram[1:255])

    if partial_pixels > 0:
        return True
    if transparent_pixels == 0:
        return False

    total_pixels = img.width * img.height
    if total_pixels and (transparent_pixels / total_pixels) >= AUTO_PNG_MIN_TRANSPARENT_RATIO:
        return True

    if img.width <= AUTO_PNG_BORDER_TOLERANCE * 2 or img.height <= AUTO_PNG_BORDER_TOLERANCE * 2:
        return True

    inner_alpha = alpha.crop(
        (
            AUTO_PNG_BORDER_TOLERANCE,
            AUTO_PNG_BORDER_TOLERANCE,
            img.width - AUTO_PNG_BORDER_TOLERANCE,
            img.height - AUTO_PNG_BORDER_TOLERANCE,
        )
    )
    inner_histogram = inner_alpha.histogram()
    return sum(inner_histogram[:255]) > 0


def flatten_for_jpeg(
    img: Image.Image,
    background: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    if "A" not in img.getbands():
        return img.convert("RGB")

    flattened = Image.new("RGB", img.size, background)
    flattened.paste(img, mask=img.getchannel("A"))
    return flattened


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
        return normalize_source_format(source_format), source_ext.lower()

    raise ValueError("OUTPUT_FORMAT must be one of: original, auto, jpeg, jpg, png, webp")


def maybe_quantize_png(img: Image.Image, colors: int, use_dither: bool) -> Image.Image:
    if colors <= 0:
        return img

    dither = DITHER_FLOYDSTEINBERG if use_dither else DITHER_NONE
    prepared = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    method = QUANTIZE_FASTOCTREE if "A" in prepared.getbands() else QUANTIZE_MEDIANCUT

    try:
        return prepared.quantize(colors=colors, method=method, dither=dither)
    except ValueError:
        print("    [WARN] PNG quantization failed; saving full-color PNG instead.")
        return img


def build_save_plan(
    result: Image.Image,
    source_ext: str,
    source_format: str | None,
    job: CompressJob,
    pad_color: tuple[int, ...],
) -> SavePlan:
    contains_transparency = has_transparency(result)
    meaningful_transparency = has_meaningful_transparency(result)
    format_name, out_suffix = resolve_output_format(
        job.output_format,
        source_ext,
        source_format,
        prefer_png_for_transparency=meaningful_transparency,
    )
    target_format = format_name

    if job.output_format.strip().lower() == "auto":
        if meaningful_transparency:
            print("    [INFO] Auto format selected PNG because transparency was detected.")
        elif contains_transparency:
            print("    [INFO] Auto format ignored minor edge-only transparency and selected JPEG.")
        else:
            print("    [INFO] Auto format selected JPEG because the image is fully opaque.")

    if target_format == "JPEG":
        if contains_transparency:
            print(
                "    [INFO] JPEG does not support transparency; "
                f"flattening alpha onto {pad_color[:3]}."
            )
        prepared = flatten_for_jpeg(result, pad_color[:3]).convert("RGB")
        return SavePlan(
            image=prepared,
            format_name=target_format,
            suffix=out_suffix,
            save_kwargs={
                "quality": job.jpeg_quality,
                "optimize": True,
                "progressive": job.jpeg_progressive,
                "subsampling": job.jpeg_subsampling,
            },
        )

    if target_format == "PNG":
        prepared = maybe_quantize_png(result, job.png_quantize_colors, job.png_dither)
        return SavePlan(
            image=prepared,
            format_name=target_format,
            suffix=out_suffix,
            save_kwargs={
                "optimize": True,
                "compress_level": job.png_compress_level,
            },
        )

    if target_format == "WEBP":
        return SavePlan(
            image=result,
            format_name=target_format,
            suffix=out_suffix,
            save_kwargs={
                "quality": job.webp_quality,
                "method": job.webp_method,
                "lossless": job.webp_lossless,
            },
        )

    return SavePlan(image=result, format_name=target_format, suffix=out_suffix, save_kwargs={})


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


def save_plan_to_path(plan: SavePlan, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if plan.format_name:
        plan.image.save(out_path, format=plan.format_name, **plan.save_kwargs)
    else:
        plan.image.save(out_path, **plan.save_kwargs)


def format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    amount = float(size)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024

    return f"{size} B"


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


def prompt_compression_value(default: int) -> int:
    while True:
        raw = input(f"Compression value 1-100 (Higher = smaller file) [{default}]: ").strip()
        if not raw:
            return default

        try:
            value = int(raw)
        except ValueError:
            print("Please enter a whole number from 1 to 100.")
            continue

        if 1 <= value <= 100:
            return value

        print("Please enter a whole number from 1 to 100.")


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
        "2": "auto     - use PNG for real transparency, otherwise JPEG",
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


def load_compress_job() -> CompressJob:
    env_path = load_environment()
    compression = get_compression_setting()
    return CompressJob(
        env_path=env_path,
        input_dir=resolve_config_path(os.getenv("INPUT_DIR", "input"), "input"),
        output_dir=resolve_config_path(
            os.getenv("COMPRESS_OUTPUT_DIR", "compress_output"),
            "compress_output",
        ),
        compression_value=compression,
        output_format=os.getenv("OUTPUT_FORMAT", "original").strip().lower() or "original",
        pad_color_raw=os.getenv("PAD_COLOR", ""),
        jpeg_quality=compression_to_quality(compression),
        jpeg_progressive=parse_bool(os.getenv("JPEG_PROGRESSIVE", "true"), True),
        jpeg_subsampling=parse_jpeg_subsampling(os.getenv("JPEG_SUBSAMPLING", "4:2:0")),
        png_compress_level=percent_to_native_scale(compression, 9),
        png_quantize_colors=clamp_int(os.getenv("PNG_QUANTIZE_COLORS", "0"), 0, 0, 256),
        png_dither=parse_bool(os.getenv("PNG_DITHER", "false"), False),
        webp_quality=compression_to_quality(compression),
        webp_method=percent_to_native_scale(compression, 6),
        webp_lossless=parse_bool(os.getenv("WEBP_LOSSLESS", "false"), False),
        overwrite=True,
    )


def validate_compress_job(job: CompressJob) -> None:
    resolve_output_format(job.output_format, ".png")
    if not 1 <= job.compression_value <= 100:
        raise ValueError("Compression value must be between 1 and 100.")


def prompt_compress_job(defaults: CompressJob) -> CompressJob:
    input_dir = prompt_path("Input image or folder", defaults.input_dir)
    output_dir = prompt_path("Output folder", defaults.output_dir)
    compression_value = prompt_compression_value(defaults.compression_value)
    output_format = prompt_output_format(defaults.output_format)
    overwrite = prompt_yes_no("Overwrite existing files", defaults.overwrite)

    return CompressJob(
        env_path=defaults.env_path,
        input_dir=input_dir,
        output_dir=output_dir,
        compression_value=compression_value,
        output_format=output_format,
        pad_color_raw=defaults.pad_color_raw,
        jpeg_quality=compression_to_quality(compression_value),
        jpeg_progressive=defaults.jpeg_progressive,
        jpeg_subsampling=defaults.jpeg_subsampling,
        png_compress_level=percent_to_native_scale(compression_value, 9),
        png_quantize_colors=defaults.png_quantize_colors,
        png_dither=defaults.png_dither,
        webp_quality=compression_to_quality(compression_value),
        webp_method=percent_to_native_scale(compression_value, 6),
        webp_lossless=defaults.webp_lossless,
        overwrite=overwrite,
    )


def run_compress(job: CompressJob) -> CompressionSummary:
    validate_compress_job(job)
    images = collect_images(job.input_dir, exclude_roots=[job.output_dir])

    if not images:
        print(f"[WARN] No supported images found in '{job.input_dir.resolve()}'")
        return CompressionSummary()

    summary = CompressionSummary()
    reserved_outputs: set[str] = set()
    job.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{SEPARATOR}")
    print("Compress Images")
    print(f"{SEPARATOR}")
    print(f"  Config : {job.env_path if job.env_path else 'default environment'}")
    print(f"  Input  : {job.input_dir.resolve()}")
    print(f"  Output : {job.output_dir.resolve()}")
    print(f"  Format : {job.output_format}")
    print(f"  Compression: {job.compression_value}/100")
    print(f"{SEPARATOR}\n")

    for img_path in images:
        label = img_path.name if job.input_dir.is_file() else img_path.relative_to(job.input_dir)
        print(f"[IMAGE] {label}")

        try:
            with Image.open(img_path) as original_img:
                source_ext = img_path.suffix.lower()
                source_format = normalize_source_format(original_img.format)
                canonical_suffix = canonical_suffix_for_format(source_format, source_ext)
                original_size = img_path.stat().st_size
                img = normalize_image(original_img)
                pad_color = parse_padding_color(job.pad_color_raw, "A" in img.getbands())

                if source_format and canonical_suffix != source_ext:
                    info_message = (
                        f"    [INFO] File extension {source_ext} contains {source_format} data."
                    )
                    if job.output_format == "original":
                        info_message += " Original mode keeps the source filename unchanged."
                    print(info_message)

                plan = build_save_plan(
                    result=img,
                    source_ext=source_ext,
                    source_format=source_format,
                    job=job,
                    pad_color=pad_color,
                )
                out_path, renamed_for_collision = build_unique_output_path(
                    source_root=job.input_dir,
                    img_path=img_path,
                    output_root=job.output_dir,
                    suffix=plan.suffix,
                    reserved_paths=reserved_outputs,
                )
                if renamed_for_collision:
                    print(
                        "    [INFO] Adjusted output name to avoid overwriting another source file: "
                        f"{out_path.name}"
                    )

                if out_path.exists() and not job.overwrite:
                    print(f"    [SKIP] {out_path}")
                    summary.skipped += 1
                    continue

                save_plan_to_path(plan, out_path)
                output_size = out_path.stat().st_size
                if (
                    output_size >= original_size
                    and plan.format_name
                    and plan.format_name == source_format
                ):
                    shutil.copyfile(img_path, out_path)
                    output_size = out_path.stat().st_size
                    print("    [INFO] Re-encoded file was not smaller; kept the original bytes instead.")
                delta = output_size - original_size
                delta_pct = (delta / original_size * 100) if original_size else 0.0
                direction = "+" if delta > 0 else ""

                print(
                    f"    [OK] Saved -> {out_path}  "
                    f"({format_bytes(original_size)} -> {format_bytes(output_size)}, "
                    f"{direction}{delta_pct:.1f}%)"
                )
                summary.saved += 1
                summary.input_bytes += original_size
                summary.output_bytes += output_size

        except Exception as exc:
            print(f"    [ERROR] {img_path.name}: {exc}")
            summary.failed += 1

        print()

    print(SEPARATOR)
    print(
        f"[OK] Done. Saved {summary.saved} file(s), "
        f"skipped {summary.skipped}, failed {summary.failed}."
    )
    if summary.saved:
        total_delta = summary.output_bytes - summary.input_bytes
        total_pct = (total_delta / summary.input_bytes * 100) if summary.input_bytes else 0.0
        direction = "+" if total_delta > 0 else ""
        print(
            f"     Total size: {format_bytes(summary.input_bytes)} -> "
            f"{format_bytes(summary.output_bytes)} ({direction}{total_pct:.1f}%)"
        )
    print(SEPARATOR)
    return summary


def main() -> None:
    defaults = load_compress_job()
    print(SEPARATOR)
    print("Interactive Image Compression Tool")
    print(SEPARATOR)
    run_compress(prompt_compress_job(defaults))


__all__ = [
    "BASE_DIR",
    "CompressJob",
    "CompressionSummary",
    "JobSummary",
    "SEPARATOR",
    "compression_to_quality",
    "load_compress_job",
    "percent_to_native_scale",
    "prompt_compress_job",
    "resolve_output_format",
    "run_compress",
]


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Cancelled by user.")
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)
