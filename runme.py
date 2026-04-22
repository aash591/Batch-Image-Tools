from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from blur import (
    BLUR_MODE_LABELS,
    BlurJob,
    format_setting_value,
    load_blur_job,
    parse_blur_values,
    run_blur,
)
from compress import (
    CompressJob,
    compression_to_quality,
    load_compress_job,
    percent_to_native_scale,
    run_compress,
)
from resize import (
    ResizeJob,
    ResizeTarget,
    SEPARATOR,
    VALID_ANCHORS,
    VALID_RESIZE_MODES,
    build_targets,
    format_target_resolution,
    load_resize_job,
    parse_output_folder_labels,
    parse_resolutions,
    run_resize,
)

BASE_DIR = Path(__file__).resolve().parent

WORKFLOW_OPTIONS = {
    "1": "Resize",
    "2": "Blur",
    "3": "Compress",
    "4": "Resize -> Blur",
    "5": "Resize -> Compress",
    "6": "Blur -> Compress",
    "7": "Resize -> Blur -> Compress",
    "8": "Exit",
}

OUTPUT_FORMAT_VALUES = {
    "1": "original",
    "2": "auto",
    "3": "jpeg",
    "4": "png",
    "5": "webp",
}

DEFAULT_BLUR_VALUE_STRINGS = {
    "gaussian": "2",
    "box": "2",
    "median": "3",
    "simple": "1",
}


@dataclass(frozen=True)
class ResizeStageConfig:
    targets: list[ResizeTarget]
    resize_mode: str
    anchor: str


@dataclass(frozen=True)
class BlurStageConfig:
    blur_mode: str
    blur_values: list[int | float]


@dataclass(frozen=True)
class CompressStageConfig:
    compression_value: int
    output_format: str


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
    labels = {
        "1": "original - keep the source format",
        "2": "auto     - use PNG for real transparency, otherwise JPEG",
        "3": "jpeg     - force JPEG output",
        "4": "png      - force PNG output",
        "5": "webp     - force WebP output",
    }
    default_key = next((key for key, value in OUTPUT_FORMAT_VALUES.items() if value == default), "1")
    choice = prompt_choice(
        "Choose a compression mode:",
        labels,
        default_key,
        input_label="Compression mode",
    )
    return OUTPUT_FORMAT_VALUES[choice]


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


def format_blur_values(values: list[int | float]) -> str:
    return ",".join(format_setting_value(value) for value in values)


def prompt_blur_values(mode: str, default_values: list[int | float] | None = None) -> list[int | float]:
    prompts = {
        "gaussian": "Gaussian radius values (example: 1,2.5,5)",
        "box": "Box blur radius values (example: 1,3,8)",
        "median": "Median filter sizes (odd numbers, example: 3,5,7)",
        "simple": "Simple blur repeat counts (whole numbers, example: 1,2,4)",
    }
    help_text = "Tip: enter one value for one output, or use commas for multiple outputs."
    default_raw = (
        format_blur_values(default_values)
        if default_values
        else DEFAULT_BLUR_VALUE_STRINGS[mode]
    )

    while True:
        raw = prompt_text(prompts[mode], default_raw, help_text=help_text)
        try:
            return parse_blur_values(mode, raw)
        except ValueError as exc:
            print(f"Invalid input: {exc}")


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


def prompt_resize_stage(defaults: ResizeJob) -> ResizeStageConfig:
    if not defaults.targets:
        env_name = defaults.env_path if defaults.env_path else BASE_DIR / ".env"
        print(f"[INFO] TARGET_RESOLUTIONS is not set in {env_name}. You can enter targets manually.")

    resolutions = prompt_target_resolutions(defaults)
    resize_mode = prompt_resize_mode(defaults.resize_mode)
    anchor = defaults.anchor
    if resize_mode in {"cover", "pad"}:
        anchor = prompt_anchor(defaults.anchor)
    targets = prompt_targets_for_folder_naming(resolutions, defaults)
    return ResizeStageConfig(targets=targets, resize_mode=resize_mode, anchor=anchor)


def prompt_blur_stage(defaults: BlurJob) -> BlurStageConfig:
    blur_mode = prompt_blur_mode(defaults.blur_mode)
    default_values = defaults.blur_values if blur_mode == defaults.blur_mode else None
    blur_values = prompt_blur_values(blur_mode, default_values)
    return BlurStageConfig(blur_mode=blur_mode, blur_values=blur_values)


def prompt_compress_stage(defaults: CompressJob) -> CompressStageConfig:
    compression_value = prompt_compression_value(defaults.compression_value)
    output_format = prompt_output_format(defaults.output_format)
    return CompressStageConfig(
        compression_value=compression_value,
        output_format=output_format,
    )


def workflow_flags(choice: str) -> tuple[bool, bool, bool]:
    mapping = {
        "1": (True, False, False),
        "2": (False, True, False),
        "3": (False, False, True),
        "4": (True, True, False),
        "5": (True, False, True),
        "6": (False, True, True),
        "7": (True, True, True),
    }
    return mapping[choice]


def default_output_dir_for_choice(
    choice: str,
    resize_defaults: ResizeJob,
    blur_defaults: BlurJob,
    compress_defaults: CompressJob,
) -> Path:
    use_resize, use_blur, use_compress = workflow_flags(choice)
    if use_compress:
        return compress_defaults.output_dir
    if use_blur:
        return blur_defaults.output_dir
    if use_resize:
        return resize_defaults.output_dir
    raise ValueError(f"Unsupported workflow choice: {choice}")


def default_overwrite_for_choice(
    choice: str,
    resize_defaults: ResizeJob,
    blur_defaults: BlurJob,
    compress_defaults: CompressJob,
) -> bool:
    use_resize, use_blur, use_compress = workflow_flags(choice)
    if use_compress:
        return compress_defaults.overwrite
    if use_blur:
        return blur_defaults.overwrite
    if use_resize:
        return resize_defaults.overwrite
    raise ValueError(f"Unsupported workflow choice: {choice}")


def build_resize_job(
    defaults: ResizeJob,
    config: ResizeStageConfig,
    input_path: Path,
    output_dir: Path,
    overwrite: bool,
) -> ResizeJob:
    return ResizeJob(
        env_path=defaults.env_path,
        raw_target_resolutions=defaults.raw_target_resolutions,
        raw_folder_labels=defaults.raw_folder_labels,
        output_folder_prefix=defaults.output_folder_prefix,
        input_path=input_path,
        output_dir=output_dir,
        targets=config.targets,
        resize_mode=config.resize_mode,
        anchor=config.anchor,
        pad_color_raw=defaults.pad_color_raw,
        overwrite=overwrite,
    )


def build_blur_job(
    defaults: BlurJob,
    config: BlurStageConfig,
    source_path: Path,
    output_dir: Path,
    overwrite: bool,
) -> BlurJob:
    return BlurJob(
        env_path=defaults.env_path,
        source_path=source_path,
        output_dir=output_dir,
        blur_mode=config.blur_mode,
        blur_values=config.blur_values,
        overwrite=overwrite,
    )


def build_compress_job(
    defaults: CompressJob,
    config: CompressStageConfig,
    input_dir: Path,
    output_dir: Path,
    overwrite: bool,
) -> CompressJob:
    return CompressJob(
        env_path=defaults.env_path,
        input_dir=input_dir,
        output_dir=output_dir,
        compression_value=config.compression_value,
        output_format=config.output_format,
        pad_color_raw=defaults.pad_color_raw,
        jpeg_quality=compression_to_quality(config.compression_value),
        jpeg_progressive=defaults.jpeg_progressive,
        jpeg_subsampling=defaults.jpeg_subsampling,
        png_compress_level=percent_to_native_scale(config.compression_value, 9),
        png_quantize_colors=defaults.png_quantize_colors,
        png_dither=defaults.png_dither,
        webp_quality=compression_to_quality(config.compression_value),
        webp_method=percent_to_native_scale(config.compression_value, 6),
        webp_lossless=defaults.webp_lossless,
        overwrite=overwrite,
    )


def execute_workflow(
    choice: str,
    source_path: Path,
    final_output_dir: Path,
    overwrite: bool,
    resize_defaults: ResizeJob,
    blur_defaults: BlurJob,
    compress_defaults: CompressJob,
    resize_config: ResizeStageConfig | None,
    blur_config: BlurStageConfig | None,
    compress_config: CompressStageConfig | None,
) -> None:
    workflow_name = WORKFLOW_OPTIONS[choice]
    print(SEPARATOR)
    print(f"Running: {workflow_name}")
    print(SEPARATOR)

    with TemporaryDirectory(prefix="image-tools-") as temp_dir_raw:
        temp_root = Path(temp_dir_raw)
        current_input = source_path

        if resize_config is not None:
            resize_output = (
                final_output_dir
                if blur_config is None and compress_config is None
                else temp_root / "resize"
            )
            run_resize(
                build_resize_job(
                    defaults=resize_defaults,
                    config=resize_config,
                    input_path=current_input,
                    output_dir=resize_output,
                    overwrite=overwrite if blur_config is None and compress_config is None else True,
                )
            )
            current_input = resize_output

        if blur_config is not None:
            blur_output = final_output_dir if compress_config is None else temp_root / "blur"
            run_blur(
                build_blur_job(
                    defaults=blur_defaults,
                    config=blur_config,
                    source_path=current_input,
                    output_dir=blur_output,
                    overwrite=overwrite if compress_config is None else True,
                )
            )
            current_input = blur_output

        if compress_config is not None:
            run_compress(
                build_compress_job(
                    defaults=compress_defaults,
                    config=compress_config,
                    input_dir=current_input,
                    output_dir=final_output_dir,
                    overwrite=overwrite,
                )
            )


def print_environment_summary(env_path: Path | None) -> None:
    print(SEPARATOR)
    print("Image Tools Runner")
    print(SEPARATOR)
    print(f"Config         : {env_path if env_path else 'default environment'}")
    print("Workflow order : resize -> blur -> compress")
    print(SEPARATOR)


def main() -> None:
    while True:
        resize_defaults = load_resize_job()
        blur_defaults = load_blur_job()
        compress_defaults = load_compress_job()
        print_environment_summary(resize_defaults.env_path)

        choice = prompt_choice(
            "Choose a workflow:",
            WORKFLOW_OPTIONS,
            "1",
            input_label="Workflow",
        )
        print()

        if choice == "8":
            break

        source_path = prompt_path("Input image or folder", resize_defaults.input_path)
        final_output_dir = prompt_path(
            "Final output folder",
            default_output_dir_for_choice(choice, resize_defaults, blur_defaults, compress_defaults),
        )
        overwrite = prompt_yes_no(
            "Overwrite existing files",
            default_overwrite_for_choice(choice, resize_defaults, blur_defaults, compress_defaults),
        )

        use_resize, use_blur, use_compress = workflow_flags(choice)

        resize_config = prompt_resize_stage(resize_defaults) if use_resize else None
        blur_config = prompt_blur_stage(blur_defaults) if use_blur else None
        compress_config = prompt_compress_stage(compress_defaults) if use_compress else None

        print()
        execute_workflow(
            choice=choice,
            source_path=source_path,
            final_output_dir=final_output_dir,
            overwrite=overwrite,
            resize_defaults=resize_defaults,
            blur_defaults=blur_defaults,
            compress_defaults=compress_defaults,
            resize_config=resize_config,
            blur_config=blur_config,
            compress_config=compress_config,
        )

        print()
        if not prompt_yes_no("Run another workflow", False):
            break
        print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Cancelled by user.")
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)
