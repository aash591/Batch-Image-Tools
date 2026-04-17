# Image Tools

- `resize.py` for batch resizing images using settings from `.env`
- `blur.py` for interactive image blur

## Install

For Termux (Android), install system dependencies first:

```bash
pkg update
pkg install libjpeg-turbo libpng freetype
```

Then install Python packages:

```bash
pip install Pillow python-dotenv
```

## Supported Images

`jpg`, `jpeg`, `png`, `webp`, `bmp`, `tiff`

## Resize Images

1. Put images in the `input/` folder.
2. Edit `.env` with the sizes and options you want.
3. Run:

```bash
python resize.py
```

Key `.env` settings:

- `TARGET_RESOLUTIONS=1080x1920,720x1280`
- `RESIZE_MODE=cover`, `cropping`, `pad`, or `contain`
- `OUTPUT_FORMAT=original`, `jpeg`, `png`, or `webp`
- `QUALITY=90`
- `OUTPUT_FOLDER_LABELS=1920x1080,1280x720` if you want custom folder names
- `OUTPUT_FOLDER_PREFIX=drawable-xxhdpi-` if you want a prefix on each output folder
- Optional advanced settings like `JPEG_PROGRESSIVE`, `JPEG_SUBSAMPLING`, `PNG_QUANTIZE_COLORS`, `PNG_DITHER`, `WEBP_LOSSLESS`, `PAD_COLOR`, and `CROP_ANCHOR`

Transparent images are resized and blurred with alpha-aware processing to avoid dark edge halos.
If you export to `jpeg`, transparency is flattened because JPEG does not support alpha.

Notes:

- `QUALITY` is the main compression knob. Higher usually means better quality and larger files.
- The sample `.env` keeps the main settings first and the optional advanced settings below them.


## Blur Images

Run:

```bash
python blur.py
```

The script will ask you for:

- source image or folder
- output folder
- blur mode
- blur strength or multiple strengths
- overwrite yes/no

Blur outputs are grouped like:

`blur_output/gaussian-2/`

