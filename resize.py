from __future__ import annotations

import os
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
ORIGINAL_RESOLUTION_TOKEN = "original"
DEFAULT_ORIGINAL_LABEL = "source"
VALID_RESIZE_MODES = {"cover", "pad", "contain"}
VALID_ANCHORS = {"center", "top", "bottom", "left", "right"}

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

if hasattr(Image, "Resampling"):
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
else:
    RESAMPLE_LANCZOS = Image.LANCZOS

ResolutionSpec = tuple[int | None, int | None]


@dataclass(frozen=True)
class ResizeTarget:
    width: int | None
    height: int | None
    folder_label: str
    folder_name: str

    @property
    def is_original(self) -> bool:
        return is_original_resolution(self.width, self.height)

    @property
    def size_label(self) -> str:
        return format_target_resolution(self.width, self.height)


@dataclass
class JobSummary:
    saved: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass(frozen=True)
class ResizeJob:
    env_path: Path | None
    raw_target_resolutions: str
    raw_folder_labels: str
    output_folder_prefix: str
    input_path: Path
    output_dir: Path
    targets: list[ResizeTarget]
    resize_mode: str
    anchor: str
    pad_color_raw: str = ""
    output_format: str = "original"
    overwrite: bool = True


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


def format_resolution(width: int, height: int) -> str:
    return f"{width}x{height}"


def is_original_resolution(width: int | None, height: int | None) -> bool:
    return width is None and height is None


def format_target_resolution(width: int | None, height: int | None) -> str:
    if is_original_resolution(width, height):
        return ORIGINAL_RESOLUTION_TOKEN
    return format_resolution(width, height)


def default_folder_label(width: int | None, height: int | None) -> str:
    if is_original_resolution(width, height):
        return DEFAULT_ORIGINAL_LABEL
    return format_resolution(width, height)


def normalize_resize_mode(raw: str) -> str:
    value = raw.strip().lower()
    aliases = {
        "cover": "cover",
        "crop": "cover",
        "cropping": "cover",
        "pad": "pad",
        "contain": "contain",
    }
    return aliases.get(value, value)


def parse_resolutions(raw: str) -> list[ResolutionSpec]:
    resolutions: list[ResolutionSpec] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue

        if part.lower() == ORIGINAL_RESOLUTION_TOKEN:
            resolutions.append((None, None))
            continue

        try:
            width, height = part.lower().split("x")
            resolutions.append((int(width), int(height)))
        except ValueError:
            print(f"[WARN] Skipping invalid resolution: '{part}'")

    return resolutions


def parse_output_folder_labels(
    raw: str,
    resolutions: list[ResolutionSpec],
) -> list[str]:
    if not resolutions:
        return []

    defaults = [default_folder_label(width, height) for width, height in resolutions]
    if not raw.strip():
        return defaults

    labels = [part.strip() for part in raw.split(",") if part.strip()]
    if not labels:
        return defaults

    if len(labels) == len(resolutions):
        return labels

    non_original_total = sum(
        1 for width, height in resolutions if not is_original_resolution(width, height)
    )
    if len(labels) == non_original_total and non_original_total != len(resolutions):
        expanded: list[str] = []
        label_iter = iter(labels)
        for width, height in resolutions:
            if is_original_resolution(width, height):
                expanded.append(DEFAULT_ORIGINAL_LABEL)
            else:
                expanded.append(next(label_iter))
        return expanded

    raise ValueError(
        "OUTPUT_FOLDER_LABELS count must match TARGET_RESOLUTIONS count, "
        "or match only the non-original targets when 'original' is included."
    )


def build_targets(
    resolutions: list[ResolutionSpec],
    folder_labels: list[str],
    prefix: str,
) -> list[ResizeTarget]:
    return [
        ResizeTarget(
            width=width,
            height=height,
            folder_label=label,
            folder_name=f"{prefix}{label}",
        )
        for (width, height), label in zip(resolutions, folder_labels)
    ]


def load_resize_job() -> ResizeJob:
    env_path = load_environment()
    raw_target_resolutions = os.getenv("TARGET_RESOLUTIONS", "").strip()
    raw_folder_labels = os.getenv("OUTPUT_FOLDER_LABELS", "").strip()
    output_folder_prefix = os.getenv("OUTPUT_FOLDER_PREFIX", "")
    resolutions = parse_resolutions(raw_target_resolutions) if raw_target_resolutions else []
    folder_labels = parse_output_folder_labels(raw_folder_labels, resolutions)
    targets = build_targets(resolutions, folder_labels, output_folder_prefix)

    return ResizeJob(
        env_path=env_path,
        raw_target_resolutions=raw_target_resolutions,
        raw_folder_labels=raw_folder_labels,
        output_folder_prefix=output_folder_prefix,
        input_path=resolve_config_path(os.getenv("INPUT_DIR", "input"), "input"),
        output_dir=resolve_config_path(os.getenv("OUTPUT_DIR", "resize_output"), "resize_output"),
        targets=targets,
        resize_mode=normalize_resize_mode(os.getenv("RESIZE_MODE", "cover")),
        anchor=os.getenv("CROP_ANCHOR", "center").strip().lower() or "center",
        pad_color_raw=os.getenv("PAD_COLOR", ""),
        output_format=os.getenv("OUTPUT_FORMAT", "original").strip().lower() or "original",
        overwrite=True,
    )


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


def resize_image(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    if "A" in img.getbands():
        return img.convert("RGBa").resize(size, RESAMPLE_LANCZOS).convert("RGBA")
    return img.resize(size, RESAMPLE_LANCZOS)


def get_crop_box(
    scaled_w: int,
    scaled_h: int,
    target_w: int,
    target_h: int,
    anchor: str,
) -> tuple[int, int, int, int]:
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
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    scaled_w = round(src_w * scale)
    scaled_h = round(src_h * scale)

    print(
        f"      scale={scale:.4f} -> {scaled_w}x{scaled_h}  "
        f"crop: {scaled_w - target_w}px horizontal, {scaled_h - target_h}px vertical"
    )
    resized = resize_image(img, (scaled_w, scaled_h))
    return resized.crop(get_crop_box(scaled_w, scaled_h, target_w, target_h, anchor))


def get_anchor_offsets(extra_w: int, extra_h: int, anchor: str) -> tuple[int, int]:
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
    src_w, src_h = img.size
    scale = min(target_w / src_w, target_h / src_h)
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
    contained = contain_resize(img, target_w, target_h)
    extra_w = target_w - contained.width
    extra_h = target_h - contained.height
    offset_x, offset_y = get_anchor_offsets(extra_w, extra_h, anchor)

    print(
        f"      pad : {extra_w}px horizontal, {extra_h}px vertical  "
        f"offset=({offset_x},{offset_y})"
    )

    canvas_mode = "RGBA" if len(pad_color) == 4 else "RGB"
    if contained.mode != canvas_mode:
        contained = contained.convert(canvas_mode)

    canvas = Image.new(canvas_mode, (target_w, target_h), pad_color)
    if "A" in contained.getbands():
        canvas.paste(contained, (offset_x, offset_y), contained.getchannel("A"))
    else:
        canvas.paste(contained, (offset_x, offset_y))
    return canvas


def apply_resize_mode(
    img: Image.Image,
    target_w: int,
    target_h: int,
    resize_mode: str,
    anchor: str,
    pad_color: tuple[int, ...],
) -> Image.Image:
    if resize_mode == "cover":
        return cover_resize(img, target_w, target_h, anchor)
    if resize_mode == "contain":
        return contain_resize(img, target_w, target_h)
    if resize_mode == "pad":
        return pad_resize(img, target_w, target_h, anchor, pad_color)
    raise ValueError("RESIZE_MODE must be one of: contain, cover, crop, cropping, pad")


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


def prompt_resize_mode(default: str) -> str:
    values = {
        "1": "cover",
        "2": "pad",
        "3": "contain",
    }
    labels = {
        "1": "cover   - fill the target size and crop overflow",
        "2": "pad     - fit the whole image and add padding",
        "3": "contain - fit the whole image without padding",
    }
    default_key = next((key for key, value in values.items() if value == default), "1")
    choice = prompt_choice("Choose a resize mode:", labels, default_key)
    selected = values[choice]
    return selected if selected in VALID_RESIZE_MODES else default


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
        "2": "auto     - use PNG for transparency, otherwise JPEG",
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


def prompt_anchor(default: str) -> str:
    values = {
        "1": "center",
        "2": "top",
        "3": "bottom",
        "4": "left",
        "5": "right",
    }
    labels = {
        "1": "center - keep the crop or padding balanced",
        "2": "top    - keep the top area, crop or pad lower",
        "3": "bottom - keep the bottom area, crop or pad higher",
        "4": "left   - keep the left area, crop or pad to the right",
        "5": "right  - keep the right area, crop or pad to the left",
    }
    default_key = next((key for key, value in values.items() if value == default), "1")
    choice = prompt_choice("Choose a crop/pad anchor:", labels, default_key)
    selected = values[choice]
    return selected if selected in VALID_ANCHORS else default


def prompt_folder_prefix(default: str) -> str:
    suffix = f" [{default}]" if default else ""

    while True:
        raw = input(f"Folder prefix{suffix} (enter '-' for none): ").strip()
        if raw == "-":
            return ""
        if raw:
            return raw
        return default


def format_env_target_resolution_preview(defaults: ResizeJob) -> str:
    if defaults.targets:
        lines = ["Use target resolutions from .env", "TARGET_RESOLUTIONS:"]
        lines.extend(f"- {target.size_label}" for target in defaults.targets)
        return "\n".join(lines)

    raw_value = defaults.raw_target_resolutions or "(not set)"
    return f"Use target resolutions from .env\nTARGET_RESOLUTIONS={raw_value}"


def format_env_folder_naming_preview(defaults: ResizeJob) -> str:
    lines = ["Read folder labels and prefix from .env", "OUTPUT_FOLDER_LABELS:"]

    if defaults.targets:
        lines.extend(f"- {target.size_label} -> {target.folder_label}" for target in defaults.targets)
    else:
        raw_labels = [label.strip() for label in defaults.raw_folder_labels.split(",") if label.strip()]
        if raw_labels:
            lines.extend(f"- {label}" for label in raw_labels)
        else:
            lines.append("- (not set)")

    prefix_value = defaults.output_folder_prefix or "(empty)"
    lines.append(f"OUTPUT_FOLDER_PREFIX={prefix_value}")
    return "\n".join(lines)


def env_folder_naming_matches_targets(
    resolutions: list[tuple[int | None, int | None]],
    defaults: ResizeJob,
) -> bool:
    env_resolutions = [(target.width, target.height) for target in defaults.targets]
    return bool(env_resolutions) and env_resolutions == resolutions


def prompt_folder_labels_individually(
    resolutions: list[tuple[int | None, int | None]],
    default_labels: list[str],
) -> list[str]:
    labels: list[str] = []
    print("Enter folder names for each target:")

    for (width, height), default_label in zip(resolutions, default_labels):
        size_label = format_target_resolution(width, height)
        labels.append(prompt_text(f"Folder name for {size_label}", default_label))

    return labels


def prompt_manual_folder_naming(
    resolutions: list[tuple[int | None, int | None]],
    defaults: ResizeJob,
) -> tuple[list[str], str]:
    default_labels = parse_output_folder_labels("", resolutions)
    labels = default_labels

    if prompt_yes_no("Set different folder names", False):
        labels = prompt_folder_labels_individually(resolutions, default_labels)

    prefix = ""
    if len(resolutions) > 1 and prompt_yes_no(
        "Apply folder prefix",
        bool(defaults.output_folder_prefix),
    ):
        prefix = prompt_folder_prefix(defaults.output_folder_prefix)

    return labels, prefix


def prompt_folder_labels_for_targets(
    resolutions: list[tuple[int | None, int | None]],
    defaults: ResizeJob,
) -> tuple[list[str], str]:
    if not env_folder_naming_matches_targets(resolutions, defaults):
        if defaults.targets:
            print("[INFO] .env folder labels do not match these target resolutions.")
            print("Using the selected sizes as default folder names instead.")
        return prompt_manual_folder_naming(resolutions, defaults)

    choice = prompt_choice(
        "Choose folder naming:",
        {
            "1": format_env_folder_naming_preview(defaults),
            "2": "Enter folder labels and prefix manually",
        },
        "1",
        input_label="Folder naming",
    )

    if choice == "1":
        try:
            labels = parse_output_folder_labels(defaults.raw_folder_labels, resolutions)
            return labels, defaults.output_folder_prefix
        except ValueError as exc:
            print(f"[WARN] {exc}")
            print("Switching to manual folder naming for these targets.")

    return prompt_manual_folder_naming(resolutions, defaults)


def prompt_targets_for_folder_naming(
    resolutions: list[tuple[int | None, int | None]],
    defaults: ResizeJob,
) -> list[ResizeTarget]:
    while True:
        folder_labels, folder_prefix = prompt_folder_labels_for_targets(resolutions, defaults)
        targets = build_targets(resolutions, folder_labels, folder_prefix)

        if targets:
            if len(targets) == 1:
                print(f"Folder name preview: {targets[0].folder_name}")
            else:
                print(
                    f"Folder name preview: {targets[0].folder_name}  "
                    f"(showing first of {len(targets)})"
                )

        if not prompt_yes_no("Edit folder naming", False):
            return targets


def prompt_target_resolutions(defaults: ResizeJob) -> list[tuple[int | None, int | None]]:
    env_has_targets = bool(defaults.targets)
    default_source = "1" if env_has_targets else "2"
    choice = prompt_choice(
        "Choose target resolutions:",
        {
            "1": format_env_target_resolution_preview(defaults),
            "2": "Enter target resolutions manually",
        },
        default_source,
        input_label="Target source",
    )

    if choice == "1" and env_has_targets:
        return [(target.width, target.height) for target in defaults.targets]

    if choice == "1":
        print("[WARN] TARGET_RESOLUTIONS is not set in .env. Switching to manual entry.")

    default_raw = defaults.raw_target_resolutions or "original,1080x1920,720x1280"
    while True:
        raw = prompt_text(
            "Target resolutions (example: original,1080x1920,720x1280)",
            default_raw,
        )
        resolutions = parse_resolutions(raw)
        if resolutions:
            return resolutions
        print("Enter at least one valid resolution.")


def prompt_resize_job(defaults: ResizeJob) -> ResizeJob:
    if not defaults.targets:
        env_name = defaults.env_path if defaults.env_path else BASE_DIR / ".env"
        print(f"[INFO] TARGET_RESOLUTIONS is not set in {env_name}. You can enter targets manually.")

    input_path = prompt_path("Input image or folder", defaults.input_path)
    output_dir = prompt_path("Output folder", defaults.output_dir)
    resolutions = prompt_target_resolutions(defaults)
    resize_mode = prompt_resize_mode(defaults.resize_mode)
    anchor = defaults.anchor
    if resize_mode in {"cover", "pad"}:
        anchor = prompt_anchor(defaults.anchor)
    output_format = prompt_output_format(defaults.output_format)
    targets = prompt_targets_for_folder_naming(resolutions, defaults)
    overwrite = prompt_yes_no("Overwrite existing files", defaults.overwrite)

    return ResizeJob(
        env_path=defaults.env_path,
        raw_target_resolutions=defaults.raw_target_resolutions,
        raw_folder_labels=defaults.raw_folder_labels,
        output_folder_prefix=defaults.output_folder_prefix,
        input_path=input_path,
        output_dir=output_dir,
        targets=targets,
        resize_mode=resize_mode,
        anchor=anchor,
        pad_color_raw=defaults.pad_color_raw,
        output_format=output_format,
        overwrite=overwrite,
    )


def validate_resize_job(job: ResizeJob) -> None:
    if not job.targets:
        env_name = job.env_path if job.env_path else BASE_DIR / ".env"
        raise ValueError(f"TARGET_RESOLUTIONS is not set in {env_name}")
    if job.resize_mode not in VALID_RESIZE_MODES:
        raise ValueError("RESIZE_MODE must be one of: contain, cover, crop, cropping, pad")
    if job.anchor not in VALID_ANCHORS:
        raise ValueError("CROP_ANCHOR must be one of: center, top, bottom, left, right")
    resolve_output_format(job.output_format, ".png")


def run_resize(job: ResizeJob) -> JobSummary:
    validate_resize_job(job)
    images = collect_images(job.input_path, exclude_roots=[job.output_dir])

    if not images:
        print(f"[WARN] No supported images found in '{job.input_path.resolve()}'")
        return JobSummary()

    summary = JobSummary()
    reserved_outputs: set[str] = set()
    job.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{SEPARATOR}")
    print("Resize Images")
    print(f"{SEPARATOR}")
    print(f"  Config : {job.env_path if job.env_path else 'default environment'}")
    print(f"  Input  : {job.input_path.resolve()}")
    print(f"  Output : {job.output_dir.resolve()}")
    print(f"  Mode   : {job.resize_mode}   Anchor: {job.anchor}")
    print(f"  Format : {job.output_format}")
    print("  Target resolutions:")
    for target in job.targets:
        if target.is_original:
            print(f"    - original size  (folder: {target.folder_name}, no resize)")
        else:
            print(
                f"    - {target.width}x{target.height}  "
                f"(folder: {target.folder_name}, aspect {target.width / target.height:.4f})"
            )
    print(f"{SEPARATOR}\n")

    for img_path in images:
        label = img_path.name if job.input_path.is_file() else img_path.relative_to(job.input_path)
        print(f"[IMAGE] {label}")

        try:
            with Image.open(img_path) as original_img:
                img = normalize_image(original_img)
                pad_color = parse_padding_color(job.pad_color_raw, "A" in img.getbands())
                src_w, src_h = img.size
                source_ext = img_path.suffix.lower()
                source_format = normalize_source_format(original_img.format)
                canonical_suffix = canonical_suffix_for_format(source_format, source_ext)

                print(f"    Source: {src_w}x{src_h}  (aspect {src_w / src_h:.4f})")
                if source_format and canonical_suffix != source_ext and job.output_format == "original":
                    print(
                        f"    [INFO] File extension {source_ext} contains {source_format} data. "
                        "Resize keeps the source filename unchanged."
                    )

                for target in job.targets:
                    if target.is_original:
                        print(f"    -> {target.folder_name} (original size)")
                        result = img.copy()
                    else:
                        print(f"    -> {target.folder_name} ({target.width}x{target.height})")
                        result = apply_resize_mode(
                            img=img,
                            target_w=target.width,
                            target_h=target.height,
                            resize_mode=job.resize_mode,
                            anchor=job.anchor,
                            pad_color=pad_color,
                        )

                    prepared, format_name, suffix = prepare_save(
                        result=result,
                        source_ext=source_ext,
                        source_format=source_format,
                        output_format=job.output_format,
                        pad_color=pad_color,
                    )
                    out_path, renamed_for_collision = build_unique_output_path(
                        source_root=job.input_path,
                        img_path=img_path,
                        output_root=job.output_dir / target.folder_name,
                        suffix=suffix,
                        reserved_paths=reserved_outputs,
                    )
                    if renamed_for_collision:
                        print(
                            "      [INFO] Adjusted output name to avoid overwriting another source file: "
                            f"{out_path.name}"
                        )

                    if out_path.exists() and not job.overwrite:
                        print(f"      [SKIP] {out_path}")
                        summary.skipped += 1
                        continue

                    save_image_to_path(prepared, out_path, format_name)
                    print(f"      [OK] Saved -> {out_path}")
                    summary.saved += 1

        except Exception as exc:
            print(f"    [ERROR] {img_path.name}: {exc}")
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
    defaults = load_resize_job()
    print(SEPARATOR)
    print("Interactive Resize Tool")
    print(SEPARATOR)
    run_resize(prompt_resize_job(defaults))


__all__ = [
    "BASE_DIR",
    "DEFAULT_ORIGINAL_LABEL",
    "JobSummary",
    "ORIGINAL_RESOLUTION_TOKEN",
    "ResizeJob",
    "ResizeTarget",
    "SEPARATOR",
    "VALID_ANCHORS",
    "VALID_RESIZE_MODES",
    "build_targets",
    "default_folder_label",
    "format_target_resolution",
    "load_resize_job",
    "normalize_resize_mode",
    "parse_output_folder_labels",
    "parse_resolutions",
    "prompt_resize_job",
    "resolve_output_format",
    "run_resize",
]


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Cancelled by user.")
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)
