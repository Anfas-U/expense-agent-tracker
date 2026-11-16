"""Unit tests for Phase 3: Receipt Ingestion, Image Compression, and Multimodal Extraction."""

from pathlib import Path
from PIL import Image
from src.storage.receipt_store import ReceiptStore
from src.tools.receipt_scanner import extract_receipt
from src.storage.local_db import get_local_db


def test_receipt_store_image_preprocessing(tmp_path: Path):
    """Verify that ReceiptStore resizes and compresses an oversized test image."""
    store = ReceiptStore(storage_dir=tmp_path / "receipts")

    # Create dummy 3000x2000 test image
    raw_img_path = tmp_path / "raw_receipt.png"
    img = Image.new("RGB", (3000, 2000), color=(255, 255, 255))
    img.save(raw_img_path)

    # Process
    processed_path = store.save_and_preprocess(raw_img_path, max_dimension=1920)
    assert processed_path.exists()
    assert processed_path.suffix == ".jpg"

    # Verify downscaled dimensions
    with Image.open(processed_path) as res_img:
        assert max(res_img.size) <= 1920


def test_extract_receipt_flow(tmp_path: Path):
    """Verify extract_receipt creates staging state and returns confirmation prompt."""
    test_img_path = tmp_path / "sample_bill.jpg"
    img = Image.new("RGB", (600, 800), color=(240, 240, 240))
    img.save(test_img_path)

    result = extract_receipt(str(test_img_path))
    assert result["status"] == "success"
    assert result["amount"] > 0
    assert "Found:" in result["confirmation_prompt"]
    assert "Save it?" in result["confirmation_prompt"]

    # Verify staging in SQLite
    local_db = get_local_db()
    with local_db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM session_state WHERE key = 'pending_receipt_entry'")
        row = cursor.fetchone()
        assert row is not None
        assert "Big Bazaar" in row["value"]
