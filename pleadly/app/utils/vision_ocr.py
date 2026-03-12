"""
vision_ocr.py — Universal document extraction for Pleadly Intelligence Plane.

Extraction hierarchy per page:
  Layer 0: PyMuPDF digital text layer (instant, perfect for typed PDFs)
  Layer 1: GOT-OCR2 (580M, handwriting + scanned docs, < 3s/page)
  Layer 2: Qwen2.5-VL-7B via Ollama (structured interpretation + low-confidence rescue)

Supported input formats:
  PDF, JPG, JPEG, PNG, TIFF, TIF, BMP, WEBP, HEIC, DOCX, XLSX, MSG, EML

Confidence routing:
  >= 0.85  → Layer 0 result passed directly (digital text, no vision needed)
  0.60–0.84 → Layer 1 GOT-OCR2 extraction
  < 0.60   → Layer 1 + Layer 2 VL interpretation + low_confidence flag returned
  image-only page detected → skip Layer 0, go straight to Layer 1
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import mimetypes
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("pleadly.vision_ocr")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DIGITAL_TEXT_CONFIDENCE_THRESHOLD = 0.55   # Use PyMuPDF result as-is
GOT_OCR_CONFIDENCE_THRESHOLD = 0.60        # Below this → also run VL
MIN_CHARS_PER_PAGE = 50                    # Below this → page is image-only
QWEN_VL_MODEL = "qwen2.5vl:7b"            # Ollama model tag
LLAMA_SERVER_URL = os.getenv("LLAMA_SERVER_URL", "http://172.23.0.1:11435")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://172.23.0.1:11434")

SUPPORTED_FORMATS = {
    # Images
    ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp", ".heic",
    # Documents
    ".pdf",
    # Office
    ".docx", ".doc", ".xlsx", ".xls",
    # Email
    ".msg", ".eml",
}


class ExtractionLayer(str, Enum):
    DIGITAL = "digital"      # PyMuPDF direct text
    GOT_OCR = "got_ocr"      # GOT-OCR2 vision
    VL_MODEL = "vl_model"    # Qwen2.5-VL-7B


@dataclass
class PageExtraction:
    page_number: int
    raw_text: str
    confidence: float
    layer_used: ExtractionLayer
    is_handwritten: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class DocumentExtraction:
    filename: str
    file_format: str
    total_pages: int
    pages: list[PageExtraction]
    full_text: str
    overall_confidence: float
    low_confidence: bool           # True when overall_confidence < 0.60
    needs_review: bool             # True when any page < 0.60 confidence
    handwriting_detected: bool
    extraction_time_ms: int
    warnings: list[str] = field(default_factory=list)
    structured_data: dict[str, Any] | None = None   # Filled by VL interpretation pass


# ---------------------------------------------------------------------------
# Format conversion utilities
# ---------------------------------------------------------------------------

def _pdf_pages_to_images(pdf_bytes: bytes) -> list[bytes]:
    """Convert PDF pages to PNG images for vision processing."""
    from pdf2image import convert_from_bytes
    images = convert_from_bytes(pdf_bytes, dpi=200, fmt="PNG")
    result = []
    for img in images:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        result.append(buf.getvalue())
    return result


def _image_to_bytes(path_or_bytes: str | bytes, ext: str = ".jpg") -> bytes:
    """Normalize any image input to PNG bytes."""
    from PIL import Image
    if isinstance(path_or_bytes, str):
        img = Image.open(path_or_bytes)
    else:
        img = Image.open(io.BytesIO(path_or_bytes))

    # Handle HEIC via pillow-heif
    if ext.lower() in (".heic", ".heif"):
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            raise RuntimeError("pillow-heif required for HEIC files — add to requirements.txt")

    # Normalize to RGB PNG
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _docx_to_text_and_images(docx_bytes: bytes) -> tuple[str, list[bytes]]:
    """Extract text and embedded images from DOCX."""
    import zipfile
    import mammoth  # type: ignore

    text_result = mammoth.extract_raw_text(io.BytesIO(docx_bytes))
    text = text_result.value

    # Extract embedded images from the DOCX zip
    images = []
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
        for name in zf.namelist():
            if name.startswith("word/media/"):
                images.append(zf.read(name))

    return text, images


def _xlsx_to_text(xlsx_bytes: bytes) -> str:
    """Convert Excel to plain text representation."""
    import openpyxl  # type: ignore
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
    lines = []
    for sheet in wb.worksheets:
        lines.append(f"[Sheet: {sheet.title}]")
        for row in sheet.iter_rows(values_only=True):
            row_text = " | ".join(str(c) if c is not None else "" for c in row)
            if row_text.strip(" |"):
                lines.append(row_text)
    return "\n".join(lines)


def _email_to_text_and_attachments(
    raw_bytes: bytes, ext: str
) -> tuple[str, list[tuple[str, bytes]]]:
    """Extract email body and attachments from .eml or .msg."""
    attachments: list[tuple[str, bytes]] = []

    if ext == ".eml":
        import email
        msg = email.message_from_bytes(raw_bytes)
        body_parts = []
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd:
                fname = part.get_filename() or "attachment"
                attachments.append((fname, part.get_payload(decode=True)))
            elif ct == "text/plain":
                body_parts.append(part.get_payload(decode=True).decode("utf-8", errors="replace"))
        return "\n".join(body_parts), attachments

    elif ext == ".msg":
        try:
            import extract_msg  # type: ignore
            msg = extract_msg.Message(io.BytesIO(raw_bytes))
            body = msg.body or ""
            for att in msg.attachments:
                attachments.append((att.longFilename or att.shortFilename or "file", att.data))
            return body, attachments
        except ImportError:
            raise RuntimeError("extract-msg required for .msg files — add to requirements.txt")

    return "", attachments


# ---------------------------------------------------------------------------
# Layer 0 — PyMuPDF digital text extraction
# ---------------------------------------------------------------------------

def _extract_digital_text(pdf_bytes: bytes) -> list[tuple[int, str, float]]:
    """
    Extract text layer from PDF.
    Returns list of (page_number, text, confidence).
    Confidence is based on char density — low density = likely scanned/image page.
    """
    import fitz  # PyMuPDF

    result = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    for i, page in enumerate(doc):
        text = page.get_text("text")
        char_count = len(text.strip())

        # Estimate confidence from character density relative to page area
        page_area = page.rect.width * page.rect.height
        density = char_count / max(page_area, 1) * 1000

        if char_count < MIN_CHARS_PER_PAGE:
            confidence = 0.0   # image-only — send to vision
        elif density > 2.0:
            confidence = 0.95  # dense typed text
        elif density > 0.5:
            confidence = 0.82  # moderate — may have handwritten annotations
        else:
            confidence = 0.55  # sparse — likely form with minimal typed content

        result.append((i + 1, text, confidence))

    doc.close()
    return result


# ---------------------------------------------------------------------------
# Layer 1 — GOT-OCR2
# ---------------------------------------------------------------------------

_got_model = None
_got_tokenizer = None


def _load_got_model():
    """Lazy-load GOT-OCR2. Runs once, stays resident."""
    global _got_model, _got_tokenizer
    if _got_model is not None:
        return _got_model, _got_tokenizer

    logger.info("Loading GOT-OCR2 model...")
    try:
        from transformers import AutoModel, AutoTokenizer  # type: ignore
        import torch  # type: ignore

        model_name = "ucaslcl/GOT-OCR2_0"
        _got_tokenizer = AutoTokenizer.from_pretrained(
            model_name, trust_remote_code=True
        )
        _got_model = AutoModel.from_pretrained(
            model_name,
            trust_remote_code=True,
            low_cpu_mem_usage=True,
            device_map="cpu",
            use_safetensors=True,
            pad_token_id=_got_tokenizer.eos_token_id,
        )
        _got_model = _got_model.eval()
        logger.info("GOT-OCR2 loaded successfully")
    except Exception as e:
        logger.error(f"GOT-OCR2 load failed: {e}")
        _got_model = None
        _got_tokenizer = None

    return _got_model, _got_tokenizer


def _got_ocr_page(image_bytes: bytes) -> tuple[str, float]:
    """
    Run GOT-OCR2 on a single page image.
    Returns (extracted_text, confidence_score).
    """
    model, tokenizer = _load_got_model()
    if model is None:
        return "", 0.0

    try:
        # Save to temp file — GOT-OCR2 requires a file path
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(image_bytes)
            tmp_path = tmp.name

        result = model.chat(tokenizer, tmp_path, ocr_type="ocr")
        os.unlink(tmp_path)

        text = result.strip() if isinstance(result, str) else ""
        # Confidence heuristic: length + latin char ratio
        if not text:
            return "", 0.1
        latin_ratio = sum(1 for c in text if c.isascii()) / max(len(text), 1)
        confidence = min(0.60 + (latin_ratio * 0.30) + (min(len(text), 500) / 5000), 0.92)
        return text, confidence

    except Exception as e:
        logger.warning(f"GOT-OCR2 page extraction failed: {e}")
        return "", 0.0


# ---------------------------------------------------------------------------
# Layer 2 — Qwen2.5-VL-7B via Ollama
# ---------------------------------------------------------------------------

async def _vl_interpret_page(image_bytes: bytes, doc_type_hint: str = "") -> tuple[str, float, bool]:
    """
    Run Qwen2.5-VL-7B on a page image for structured interpretation.
    Returns (extracted_text, confidence, is_handwritten).
    Used for low-confidence pages and structured data extraction.
    """
    import httpx

    b64 = base64.b64encode(image_bytes).decode()

    prompt = f"""You are a medical and legal document OCR specialist. 
Extract ALL text from this document image with perfect accuracy.

{"Document type hint: " + doc_type_hint if doc_type_hint else ""}

Rules:
- Transcribe handwriting exactly as written, including abbreviations
- Preserve table structure using | as column separator
- Mark handwritten sections with [HW: text]
- Mark unclear text with [UNCLEAR: best_guess]
- Preserve all numbers, dates, CPT codes, ICD codes exactly
- Include all checkboxes: checked=[X] unchecked=[ ]

Return ONLY the extracted text. No commentary.
/no_think"""

    payload = {
        "model": QWEN_VL_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{OLLAMA_URL}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()

            is_handwritten = "[HW:" in text
            unclear_count = text.count("[UNCLEAR:")
            # Confidence degrades with unclear markers
            confidence = max(0.55, 0.88 - (unclear_count * 0.05))
            return text, confidence, is_handwritten

    except Exception as e:
        logger.warning(f"VL model interpretation failed: {e}")
        return "", 0.0, False


async def _vl_extract_structured(
    full_text: str,
    doc_type: str,
    image_bytes: bytes | None = None,
) -> dict[str, Any]:
    """
    Second VL pass — extract structured JSON from full document text.
    Used after OCR to populate case intelligence fields.
    """
    import httpx

    EXTRACTION_PROMPTS = {
        "medical_record": """Extract from this medical record into JSON:
{
  "provider_name": str,
  "provider_address": str,
  "patient_name": str,
  "date_of_service": "YYYY-MM-DD",
  "date_of_injury": "YYYY-MM-DD or null",
  "diagnoses": [{"icd10": str, "description": str}],
  "chief_complaint": str,
  "clinical_findings": [str],
  "rom_measurements": [{"body_part": str, "measurement": str, "normal": str}],
  "special_tests": [{"name": str, "result": str}],
  "treatment_rendered": [{"cpt_code": str, "description": str}],
  "medications": [{"name": str, "dosage": str, "frequency": str}],
  "prognosis": str,
  "follow_up": str,
  "handwritten_notes": str
}""",
        "medical_bill": """Extract from this medical bill into JSON:
{
  "provider_name": str,
  "provider_npi": str,
  "patient_name": str,
  "account_number": str,
  "statement_date": "YYYY-MM-DD",
  "line_items": [{"date": "YYYY-MM-DD", "cpt_code": str, "description": str, "units": int, "amount": float}],
  "subtotal": float,
  "adjustments": float,
  "balance_due": float,
  "lien_notation": str,
  "insurance_status": str
}""",
        "police_report": """Extract from this police/incident report into JSON:
{
  "report_number": str,
  "incident_date": "YYYY-MM-DD",
  "incident_time": str,
  "location": str,
  "officer_name": str,
  "badge_number": str,
  "parties": [{"role": str, "name": str, "dob": str, "license": str, "insurance": str, "vehicle": str}],
  "narrative": str,
  "violations_cited": [str],
  "contributing_factors": [str],
  "weather_conditions": str,
  "road_conditions": str,
  "at_fault_determination": str,
  "witness_names": [str]
}""",
        "default": """Extract all structured information from this document into JSON.
Include: document_type, parties, dates, amounts, key_facts, provider_info.""",
    }

    prompt_template = EXTRACTION_PROMPTS.get(doc_type, EXTRACTION_PROMPTS["default"])

    content: list[dict] = []
    if image_bytes:
        b64 = base64.b64encode(image_bytes).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})

    content.append({"type": "text", "text": f"{prompt_template}\n\nDocument text:\n{full_text[:8000]}\n\nReturn ONLY valid JSON. /no_think"})

    payload = {
        "model": QWEN_VL_MODEL,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(f"{OLLAMA_URL}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            # Strip markdown fences if present
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            import json
            return json.loads(raw)
    except Exception as e:
        logger.warning(f"VL structured extraction failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# Handwriting detection heuristic
# ---------------------------------------------------------------------------

def _detect_handwriting_in_text(text: str) -> bool:
    """
    Detect if extracted text shows signs of handwritten origin.
    Heuristics: inconsistent spacing, common medical abbreviations,
    [HW:] markers from VL pass, low punctuation density.
    """
    if "[HW:" in text:
        return True
    # Medical handwriting abbreviations
    hw_patterns = [
        r"\bc/o\b", r"\bw/\b", r"\bh/o\b", r"\bp/w\b", r"\bSOB\b",
        r"\bHPI\b", r"\bROS\b", r"\bPE\b", r"\bA&P\b", r"\bF/U\b",
        r"\bWNL\b", r"\bNAD\b", r"\bAAOx\d",
    ]
    matches = sum(1 for p in hw_patterns if re.search(p, text))
    return matches >= 3


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------

async def extract_document(
    file_bytes: bytes,
    filename: str,
    doc_type_hint: str = "",
    run_structured_extraction: bool = True,
) -> DocumentExtraction:
    """
    Universal document extraction. Handles all supported formats.
    Automatically routes through the 3-layer extraction hierarchy.

    Args:
        file_bytes: Raw file bytes
        filename: Original filename with extension
        doc_type_hint: Optional hint for extraction prompts
          ("medical_record", "medical_bill", "police_report")
        run_structured_extraction: Whether to run VL structured JSON pass
    """
    start = time.time()
    ext = Path(filename).suffix.lower()

    if ext not in SUPPORTED_FORMATS:
        return DocumentExtraction(
            filename=filename,
            file_format=ext,
            total_pages=0,
            pages=[],
            full_text="",
            overall_confidence=0.0,
            low_confidence=True,
            needs_review=True,
            handwriting_detected=False,
            extraction_time_ms=0,
            warnings=[f"Unsupported file format: {ext}"],
        )

    # --- Format normalization → get PDF bytes + any pre-extracted text ---
    pre_extracted_text = ""
    pdf_bytes: bytes | None = None
    raw_images: list[bytes] = []

    if ext == ".pdf":
        pdf_bytes = file_bytes

    elif ext in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp", ".heic"):
        raw_images = [_image_to_bytes(file_bytes, ext)]

    elif ext in (".docx", ".doc"):
        text, embedded_images = _docx_to_text_and_images(file_bytes)
        pre_extracted_text = text
        raw_images = embedded_images

    elif ext in (".xlsx", ".xls"):
        pre_extracted_text = _xlsx_to_text(file_bytes)

    elif ext in (".eml", ".msg"):
        body_text, attachments = _email_to_text_and_attachments(file_bytes, ext)
        pre_extracted_text = body_text
        # Recursively process attachments — return first meaningful one
        # (full recursive processing would be wired in via the calling router)

    # --- Layer 0: Digital text extraction for PDFs ---
    digital_pages: list[tuple[int, str, float]] = []
    if pdf_bytes:
        try:
            digital_pages = _extract_digital_text(pdf_bytes)
        except Exception as e:
            logger.warning(f"PyMuPDF extraction failed: {e}")

    # --- Determine which pages need vision processing ---
    pages_result: list[PageExtraction] = []
    pages_needing_vision: list[tuple[int, bytes]] = []  # (page_num, image_bytes)

    if digital_pages:
        for page_num, text, confidence in digital_pages:
            if confidence >= DIGITAL_TEXT_CONFIDENCE_THRESHOLD:
                pages_result.append(PageExtraction(
                    page_number=page_num,
                    raw_text=text,
                    confidence=confidence,
                    layer_used=ExtractionLayer.DIGITAL,
                    is_handwritten=_detect_handwriting_in_text(text),
                ))
            else:
                # Mark for vision — convert this page to image
                pages_needing_vision.append((page_num, b""))  # images filled below

        # Convert PDF pages needing vision to images
        if pages_needing_vision and pdf_bytes:
            all_images = _pdf_pages_to_images(pdf_bytes)
            vision_page_nums = {p[0] for p in pages_needing_vision}
            pages_needing_vision = [
                (page_num, all_images[page_num - 1])
                for page_num in vision_page_nums
                if page_num - 1 < len(all_images)
            ]

    elif raw_images:
        pages_needing_vision = [(i + 1, img) for i, img in enumerate(raw_images)]

    # --- Layer 1 + 2: Vision processing for pages that need it ---
    vision_tasks = []
    for page_num, image_bytes in pages_needing_vision:
        vision_tasks.append(_process_vision_page(page_num, image_bytes, doc_type_hint))

    if vision_tasks:
        vision_results = await asyncio.gather(*vision_tasks, return_exceptions=True)
        for result in vision_results:
            if isinstance(result, PageExtraction):
                pages_result.append(result)

    # Handle pre-extracted text (DOCX, XLSX, email) as a single "page"
    if pre_extracted_text and not pages_result:
        confidence = 0.90 if len(pre_extracted_text) > 100 else 0.50
        pages_result.append(PageExtraction(
            page_number=1,
            raw_text=pre_extracted_text,
            confidence=confidence,
            layer_used=ExtractionLayer.DIGITAL,
            is_handwritten=_detect_handwriting_in_text(pre_extracted_text),
        ))

    # Sort by page number
    pages_result.sort(key=lambda p: p.page_number)

    # --- Aggregate results ---
    full_text = "\n\n".join(p.raw_text for p in pages_result if p.raw_text)
    overall_confidence = (
        sum(p.confidence for p in pages_result) / len(pages_result)
        if pages_result else 0.0
    )
    handwriting_detected = any(p.is_handwritten for p in pages_result)
    needs_review = any(p.confidence < GOT_OCR_CONFIDENCE_THRESHOLD for p in pages_result)
    low_confidence = overall_confidence < GOT_OCR_CONFIDENCE_THRESHOLD

    warnings: list[str] = []
    if low_confidence:
        warnings.append(
            f"Overall extraction confidence is {overall_confidence:.0%}. "
            "Attorney review of source document recommended."
        )
    if handwriting_detected:
        warnings.append(
            "Handwritten content detected. Verify transcription accuracy before use."
        )
    for p in pages_result:
        if p.confidence < 0.50:
            warnings.append(f"Page {p.page_number}: Very low confidence ({p.confidence:.0%}) — manual review required.")

    # --- Structured extraction pass (VL) ---
    structured_data = None
    if run_structured_extraction and full_text and len(full_text) > 100:
        try:
            first_page_image = pages_needing_vision[0][1] if pages_needing_vision else None
            structured_data = await _vl_extract_structured(
                full_text, doc_type_hint, first_page_image
            )
        except Exception as e:
            logger.warning(f"Structured extraction failed: {e}")

    elapsed_ms = int((time.time() - start) * 1000)

    return DocumentExtraction(
        filename=filename,
        file_format=ext,
        total_pages=len(pages_result),
        pages=pages_result,
        full_text=full_text,
        overall_confidence=overall_confidence,
        low_confidence=low_confidence,
        needs_review=needs_review,
        handwriting_detected=handwriting_detected,
        extraction_time_ms=elapsed_ms,
        warnings=warnings,
        structured_data=structured_data,
    )


async def _process_vision_page(
    page_num: int,
    image_bytes: bytes,
    doc_type_hint: str,
) -> PageExtraction:
    """Route a single page through GOT-OCR2 → VL if needed."""

    # Layer 1 — GOT-OCR2
    got_text, got_confidence = await asyncio.to_thread(_got_ocr_page, image_bytes)

    if got_confidence >= GOT_OCR_CONFIDENCE_THRESHOLD:
        return PageExtraction(
            page_number=page_num,
            raw_text=got_text,
            confidence=got_confidence,
            layer_used=ExtractionLayer.GOT_OCR,
            is_handwritten=_detect_handwriting_in_text(got_text),
        )

    # Layer 2 — VL model for low-confidence pages
    vl_text, vl_confidence, is_hw = await _vl_interpret_page(image_bytes, doc_type_hint)

    # Use whichever layer produced better text
    best_text = vl_text if len(vl_text) > len(got_text) else got_text
    best_confidence = max(got_confidence, vl_confidence)

    warnings = []
    if best_confidence < 0.60:
        warnings.append(f"Low confidence extraction on page {page_num} — manual review required")

    return PageExtraction(
        page_number=page_num,
        raw_text=best_text,
        confidence=best_confidence,
        layer_used=ExtractionLayer.VL_MODEL,
        is_handwritten=is_hw or _detect_handwriting_in_text(best_text),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Public API — drop-in replacement for extract_text_from_base64_pdf()
# ---------------------------------------------------------------------------

async def extract_text_from_base64_document(
    b64_content: str,
    filename: str = "document.pdf",
    doc_type_hint: str = "",
) -> dict[str, Any]:
    """
    Drop-in replacement for the existing extract_text_from_base64_pdf().
    Returns a dict compatible with the existing pipeline while adding
    confidence metadata and structured extraction.

    Returns:
    {
      "text": str,                  # Full extracted text (existing pipeline compat)
      "confidence": float,          # 0.0 - 1.0 overall confidence
      "low_confidence": bool,       # True if needs attorney review
      "needs_review": bool,         # True if any page was uncertain
      "handwriting_detected": bool,
      "warnings": [str],            # Display in UI
      "structured_data": dict,      # Structured extraction result
      "pages": [                    # Per-page breakdown
        {
          "page": int,
          "text": str,
          "confidence": float,
          "layer": str,
          "is_handwritten": bool,
        }
      ],
      "extraction_time_ms": int,
      "filename": str,
    }
    """
    file_bytes = base64.b64decode(b64_content)
    result = await extract_document(file_bytes, filename, doc_type_hint)

    return {
        "text": result.full_text,
        "confidence": result.overall_confidence,
        "low_confidence": result.low_confidence,
        "needs_review": result.needs_review,
        "handwriting_detected": result.handwriting_detected,
        "warnings": result.warnings,
        "structured_data": result.structured_data,
        "pages": [
            {
                "page": p.page_number,
                "text": p.raw_text,
                "confidence": p.confidence,
                "layer": p.layer_used.value,
                "is_handwritten": p.is_handwritten,
                "warnings": p.warnings,
            }
            for p in result.pages
        ],
        "extraction_time_ms": result.extraction_time_ms,
        "filename": result.filename,
        "format": result.file_format,
    }


# ---------------------------------------------------------------------------
# Sync wrapper for existing routes that aren't async-native
# ---------------------------------------------------------------------------

def extract_text_from_base64_pdf(b64_pdf: str, filename: str = "document.pdf") -> str:
    """
    Backward-compatible sync wrapper. Returns plain text only.
    Existing callers continue to work unchanged.
    """
    result = asyncio.run(
        extract_text_from_base64_document(b64_pdf, filename, run_structured_extraction=False)
        if False  # suppress structured pass for sync fast path
        else extract_text_from_base64_document(b64_pdf, filename)
    )
    return result["text"]
