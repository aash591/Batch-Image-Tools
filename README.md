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
- `RESIZE_MODE=cover`, `pad`, or `contain`
- `OUTPUT_FORMAT=original`, `jpeg`, `png`, or `webp`


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

