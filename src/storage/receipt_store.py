"""Receipt image storage and preprocessing."""

import datetime as dt
import io
from pathlib import Path
from typing import Optional, Tuple, Union
import uuid
from PIL import Image, ImageOps

from config.settings import settings


class ReceiptStore:
    """Handles saving, compressing, and managing receipt images."""

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or settings.receipts_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def save_and_preprocess(
        self,
        image_input: Union[str, Path, bytes, io.BytesIO],
        max_dimension: int = 1920,
        quality: int = 85,
    ) -> Path:
        """Process and save receipt image with EXIF rotation, resizing, and compression.

        Args:
            image_input: File path, bytes, or BytesIO of incoming image.
            max_dimension: Max width/height to limit latency and LLM token cost.
            quality: JPEG quality (1-100).

        Returns:
            Path to the saved processed image.
        """
        # Load image with PIL
        if isinstance(image_input, (str, Path)):
            img = Image.open(image_input)
        elif isinstance(image_input, bytes):
            img = Image.open(io.BytesIO(image_input))
        else:
            img = Image.open(image_input)

        # Transpose/rotate image according to EXIF orientation
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        # Convert to RGB (in case of RGBA or palette)
        if img.mode != "RGB":
            img = img.convert("RGB")

        # Downscale if exceeding max_dimension
        width, height = img.size
        if width > max_dimension or height > max_dimension:
            if width > height:
                new_width = max_dimension
                new_height = int(height * (max_dimension / width))
            else:
                new_height = max_dimension
                new_width = int(width * (max_dimension / height))
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Generate unique filename
        filename = f"receipt_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.jpg"
        target_path = self.storage_dir / filename

        # Save with optimized JPEG compression
        img.save(target_path, "JPEG", optimize=True, quality=quality)
        return target_path


_receipt_store = ReceiptStore()


def get_receipt_store() -> ReceiptStore:
    """Get singleton ReceiptStore instance."""
    return _receipt_store
