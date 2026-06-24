"""Image operations used by the protocol runtime."""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def combine_images(background_path: Path, sprite_path: Path, output_path: Path) -> dict:
    background = Image.open(background_path).convert("RGB")
    sprite = Image.open(sprite_path).convert("RGB")

    background_width, background_height = background.size
    sprite_width, sprite_height = sprite.size
    canvas_width = max(background_width, sprite_width)
    canvas_height = background_height + sprite_height

    sample = np.array(background.crop((0, 0, 10, 10)))
    average_color = tuple(int(channel) for channel in np.mean(sample, axis=(0, 1)))
    canvas = Image.new("RGB", (canvas_width, canvas_height), average_color)
    canvas.paste(sprite, ((canvas_width - sprite_width) // 2, 0))
    canvas.paste(background, (0, sprite_height))
    canvas.save(output_path, quality=95)

    return {
        "ok": True,
        "width": canvas_width,
        "height": canvas_height,
        "sprite_height": sprite_height,
        "output": str(output_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["combine"])
    parser.add_argument("background", type=Path)
    parser.add_argument("sprite", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    result = combine_images(args.background, args.sprite, args.output)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()