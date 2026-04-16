# Multi-Resolution Image Resizer

A Python tool for resizing images to multiple resolutions with various strategies.

## Features
- Reads target resolutions from `.env` file
- Supports multiple resize strategies:
  - `cover`: Fill target and crop excess
  - `pad`: Keep full content and add padding to reach exact target size
  - `contain`: Keep full content without padding (output may be smaller)
- Configurable output compression and format conversion
- Custom output folder labels and prefixes

## Installation
Install dependencies:
```
pip install Pillow python-dotenv
```

## Usage
1. Put your source images in the `input/` folder
2. Edit `.env` to set your target resolutions
3. Run: `python resize_images.py`

## Requirements
- Python 3.x
- Pillow (PIL)
- python-dotenv