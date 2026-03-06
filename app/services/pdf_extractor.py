"""
PDF Text Extraction Service
============================
Extracts text from insurance plan PDFs using:
1. pdfplumber (primary) — handles text-based PDFs
2. pytesseract OCR (fallback) — handles scanned/image PDFs

Optimized for Indian health insurance plan documents which often contain:
- Multi-column layouts (benefits tables)
- Hindi/regional language headers with English content
- Scanned appendices and endorsements
"""

import os
import logging
from typing import List, Optional
from dataclasses import dataclass

logger = logging.getLogger("nhcx-converter.pdf")


@dataclass
class ExtractedPage:
    page_number: int
    text: str
    tables: List[List[List[str]]]  # List of tables, each is list of rows
    has_text: bool


@dataclass
class PDFExtractionResult:
    full_text: str
    pages: List[ExtractedPage]
    total_pages: int
    tables_found: int
    ocr_used: bool
    extraction_quality: str  # "high", "medium", "low"


class PDFExtractor:
    """
    Extracts structured text and tables from insurance plan PDFs.
    Handles both digital and scanned PDFs common in Indian insurance ecosystem.
    """

    def extract(self, pdf_path: str) -> PDFExtractionResult:
        """
        Extract text and tables from a PDF file.

        Strategy:
        1. Try pdfplumber for text-based PDFs
        2. If text is sparse, fall back to OCR
        3. Extract tables separately for structured benefit data
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        logger.info("Extracting text from: %s", os.path.basename(pdf_path))

        # Try pdfplumber first
        pages, tables_count, full_text = self._extract_with_pdfplumber(pdf_path)

        # Check if we got enough text
        total_chars = len(full_text.strip())
        total_pages = len(pages)

        if total_chars < 100 and total_pages > 0:
            # Very little text — likely scanned PDF, try OCR
            logger.info("Sparse text detected (%d chars), attempting OCR", total_chars)
            pages, tables_count, full_text = self._extract_with_ocr(pdf_path)
            ocr_used = True
        else:
            ocr_used = False

        # Determine extraction quality
        chars_per_page = total_chars / max(total_pages, 1)
        if chars_per_page > 500:
            quality = "high"
        elif chars_per_page > 100:
            quality = "medium"
        else:
            quality = "low"

        logger.info(
            "Extraction complete: %d pages, %d chars, %d tables, quality=%s, ocr=%s",
            total_pages, total_chars, tables_count, quality, ocr_used,
        )

        return PDFExtractionResult(
            full_text=full_text,
            pages=pages,
            total_pages=total_pages,
            tables_found=tables_count,
            ocr_used=ocr_used,
            extraction_quality=quality,
        )

    def _extract_with_pdfplumber(self, pdf_path: str):
        """Extract using pdfplumber — best for digitally-created PDFs."""
        import pdfplumber

        pages = []
        all_text_parts = []
        tables_count = 0

        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                tables = []

                # Extract tables
                try:
                    raw_tables = page.extract_tables() or []
                    for table in raw_tables:
                        cleaned = [
                            [str(cell).strip() if cell else "" for cell in row]
                            for row in table
                        ]
                        tables.append(cleaned)
                        tables_count += 1
                except Exception:
                    pass

                pages.append(ExtractedPage(
                    page_number=i + 1,
                    text=text,
                    tables=tables,
                    has_text=len(text.strip()) > 10,
                ))
                all_text_parts.append(f"\n--- Page {i + 1} ---\n{text}")

                # Add table content as text too
                for table in tables:
                    for row in table:
                        all_text_parts.append(" | ".join(row))

        full_text = "\n".join(all_text_parts)
        return pages, tables_count, full_text

    def _extract_with_ocr(self, pdf_path: str):
        """
        OCR fallback using pdf2image + pytesseract.
        For scanned insurance plan documents.
        """
        try:
            from pdf2image import convert_from_path
            import pytesseract
        except ImportError:
            logger.warning(
                "OCR dependencies not installed. "
                "Install with: pip install pdf2image pytesseract"
            )
            return [], 0, ""

        pages = []
        all_text_parts = []
        tables_count = 0

        try:
            images = convert_from_path(pdf_path, dpi=300)
            for i, img in enumerate(images):
                # OCR with Indian language support
                text = pytesseract.image_to_string(
                    img, lang="eng+hin", config="--psm 6"
                )
                pages.append(ExtractedPage(
                    page_number=i + 1,
                    text=text,
                    tables=[],
                    has_text=len(text.strip()) > 10,
                ))
                all_text_parts.append(f"\n--- Page {i + 1} (OCR) ---\n{text}")

        except Exception as e:
            logger.error("OCR extraction failed: %s", e)

        full_text = "\n".join(all_text_parts)
        return pages, tables_count, full_text

    def chunk_text(
        self,
        text: str,
        chunk_size: int = 4000,
        overlap: int = 500,
    ) -> List[str]:
        """
        Split text into overlapping chunks for LLM processing.
        Respects paragraph boundaries where possible.
        """
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size

            # Try to break at a paragraph boundary
            if end < len(text):
                newline_pos = text.rfind("\n\n", start + chunk_size // 2, end)
                if newline_pos > start:
                    end = newline_pos

            chunks.append(text[start:end].strip())
            start = end - overlap

        return chunks
