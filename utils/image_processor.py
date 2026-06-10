import os
import logging
from PIL import Image, ImageDraw, ImageFont, ImageOps

logger = logging.getLogger(__name__)

# Instagram restrictions
MIN_ASPECT = 0.8  # 4:5 (tallest allowed)
MAX_ASPECT = 1.91  # 1.91:1 (widest allowed)
MAX_WIDTH = 1440
MAX_FILE_SIZE_BYTES = 8 * 1024 * 1024  # 8MB


def get_image_info(image_path: str) -> tuple[int, int, float]:
    """Returns (width, height, aspect_ratio)."""
    with Image.open(image_path) as img:
        w, h = img.size
        return w, h, float(w) / float(h)


def validate_image(image_path: str) -> tuple[bool, str]:
    """Validates that the image file size and aspect ratios are valid for Instagram."""
    try:
        if not os.path.exists(image_path):
            return False, "File does not exist"
        
        file_size = os.path.getsize(image_path)
        if file_size > MAX_FILE_SIZE_BYTES:
            return False, f"File size ({file_size / 1024 / 1024:.2f}MB) exceeds Instagram's 8MB limit"

        w, h, aspect = get_image_info(image_path)
        if aspect < MIN_ASPECT or aspect > MAX_ASPECT:
            return False, f"Aspect ratio ({aspect:.2f}) is invalid. Instagram requires between {MIN_ASPECT} (4:5) and {MAX_ASPECT} (1.91:1)"

        return True, "Valid"
    except Exception as e:
        logger.error("Error validating image %s: %s", image_path, e)
        return False, str(e)


def resize_and_compress(image_path: str, output_path: str = None) -> str:
    """Resizes image to max width of 1440px and compresses if it is too large."""
    if output_path is None:
        output_path = image_path
        
    try:
        with Image.open(image_path) as img:
            # Convert RGBA to RGB if saving as JPEG
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGB")
            
            w, h = img.size
            if w > MAX_WIDTH:
                ratio = MAX_WIDTH / float(w)
                new_h = int(h * ratio)
                img = img.resize((MAX_WIDTH, new_h), Image.Resampling.LANCZOS)
                logger.info("Resized image %s to width %d", image_path, MAX_WIDTH)
            
            # Save and compress
            img.save(output_path, "JPEG", quality=90, optimize=True)
            
            # If still too large, compress further
            quality = 85
            while os.path.getsize(output_path) > MAX_FILE_SIZE_BYTES and quality > 50:
                img.save(output_path, "JPEG", quality=quality, optimize=True)
                quality -= 5
                
            return output_path
    except Exception as e:
        logger.error("Error compressing image %s: %s", image_path, e)
        return image_path


def fit_aspect_ratio(image_path: str, mode: str = "pad", target_ratio: float = 1.0, output_path: str = None) -> str:
    """
    Fits image to a target aspect ratio (e.g. 1.0 for 1:1, 0.8 for 4:5).
    Modes:
      - 'pad': adds solid background borders (white/black) to fit the ratio.
      - 'crop': crops the image from the center to match the ratio.
    """
    if output_path is None:
        file_dir, file_name = os.path.split(image_path)
        output_path = os.path.join(file_dir, f"fitted_{file_name}")

    try:
        with Image.open(image_path) as img:
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGB")

            w, h = img.size
            current_ratio = float(w) / float(h)

            if abs(current_ratio - target_ratio) < 0.02:
                # Already close enough, just save copy
                img.save(output_path, "JPEG", quality=95)
                return output_path

            if mode == "crop":
                # Crop from center
                if current_ratio > target_ratio:
                    # Current image is wider than target. Keep height, crop width
                    new_w = int(h * target_ratio)
                    left = (w - new_w) // 2
                    right = left + new_w
                    img_cropped = img.crop((left, 0, right, h))
                else:
                    # Current image is taller than target. Keep width, crop height
                    new_h = int(w / target_ratio)
                    top = (h - new_h) // 2
                    bottom = top + new_h
                    img_cropped = img.crop((0, top, w, bottom))
                img_cropped.save(output_path, "JPEG", quality=95)
                
            else:  # pad mode
                # Pad to fit the target ratio
                if current_ratio > target_ratio:
                    # Current image is wider than target. Need to add vertical padding
                    new_h = int(w / target_ratio)
                    background = Image.new("RGB", (w, new_h), (255, 255, 255))
                    offset = (0, (new_h - h) // 2)
                    background.paste(img, offset)
                    background.save(output_path, "JPEG", quality=95)
                else:
                    # Current image is taller than target. Need to add horizontal padding
                    new_w = int(h * target_ratio)
                    background = Image.new("RGB", (new_w, h), (255, 255, 255))
                    offset = ((new_w - w) // 2, 0)
                    background.paste(img, offset)
                    background.save(output_path, "JPEG", quality=95)

            return output_path
    except Exception as e:
        logger.error("Error refitting image %s: %s", image_path, e)
        return image_path


def apply_watermark(image_path: str, text: str, output_path: str = None) -> str:
    """Applies a semitransparent text watermark in the bottom-right corner."""
    if output_path is None:
        file_dir, file_name = os.path.split(image_path)
        output_path = os.path.join(file_dir, f"watermarked_{file_name}")

    try:
        with Image.open(image_path) as img:
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGB")
            
            # Make a copy to draw on
            watermark_img = img.copy()
            w, h = watermark_img.size
            
            # Set font size relative to image size
            font_size = max(18, int(w * 0.025))
            
            # Load font
            try:
                # Windows fonts
                font_paths = ["arial.ttf", "calibri.ttf", "msyh.ttc"]
                font = None
                for fp in font_paths:
                    try:
                        font = ImageFont.truetype(fp, font_size)
                        break
                    except IOError:
                        continue
                if not font:
                    font = ImageFont.load_default()
            except Exception:
                font = ImageFont.load_default()

            # Create transparent layer for drawing watermark text
            overlay = Image.new("RGBA", watermark_img.size, (255, 255, 255, 0))
            draw = ImageDraw.Draw(overlay)
            
            # Get text size
            try:
                # Pillow 10+ has textbbox
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
            except AttributeError:
                # Fallback for older Pillow versions
                text_width, text_height = draw.textsize(text, font=font)

            # Define bottom-right coordinates with margin
            margin_x = int(w * 0.05)
            margin_y = int(h * 0.05)
            x = w - text_width - margin_x
            y = h - text_height - margin_y
            
            # Draw semi-transparent shadow and text
            # Draw shadow
            draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 100))
            # Draw white text
            draw.text((x, y), text, font=font, fill=(255, 255, 255, 160))
            
            # Composite images
            watermark_img = Image.alpha_composite(watermark_img.convert("RGBA"), overlay)
            watermark_img.convert("RGB").save(output_path, "JPEG", quality=95)
            return output_path
    except Exception as e:
        logger.error("Error watermarking image %s: %s", image_path, e)
        return image_path
