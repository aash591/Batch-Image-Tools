# Image Tools

Simple Python scripts to resize, blur, and compress images in batch.

You can run one step at a time, or use the main runner to do multiple steps in one flow.

## What It Can Do

- Resize images
- Blur images
- Compress images
- Convert image format
- Process a single image or a whole folder

Supported formats:

- `jpg`
- `jpeg`
- `png`
- `webp`
- `bmp`
- `tiff`

## Files

- `runme.py` - main interactive tool
- `resize.py` - resize only
- `blur.py` - blur only
- `compress.py` - compress only

## Quick Start

### 1. Install Python packages

```bash
pip install Pillow python-dotenv
```

### 2. Run the main tool

```bash
python runme.py
```

### 3. Follow the prompts

The tool will ask you:

- which workflow you want
- which image or folder to use
- where to save the output
- what resize, blur, or compression settings you want

## How To Use

If you just want the easiest way:

1. Put your images in the `input` folder, or choose any image/folder when asked.
2. Run:

```bash
python runme.py
```

3. Pick a workflow:
   - `Resize`
   - `Blur`
   - `Compress`
   - or a combined workflow like `Resize -> Blur -> Compress`
4. Answer the prompts.
5. Check your output folder for the processed images.

## Run Only One Tool

If you want just one operation:

```bash
python resize.py
python blur.py
python compress.py
```

Each script opens its own simple interactive menu.

## Optional `.env` Settings

You do not need to edit `.env` to use the project.

If you want default values, you can add settings like:

```env
TARGET_RESOLUTIONS=1080x1920,720x1280
OUTPUT_FOLDER_LABELS=1920x1080,1280x720
OUTPUT_FOLDER_PREFIX=drawable-xxhdpi-
```

These values are used as defaults in the interactive prompts.


## Notes

- Folder input is processed recursively.
- Transparent images are handled correctly when possible.
- JPEG does not support transparency, so transparent areas are flattened when saving as JPEG.
