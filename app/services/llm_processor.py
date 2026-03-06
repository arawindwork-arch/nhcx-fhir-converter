"""
LLM-Powered Insurance Plan Data Extraction
============================================
Uses LangChain to extract structured insurance plan data from raw PDF text.

Supports multiple LLM providers:
  - OpenAI (GPT-4o / GPT-4o-mini) — recommended for accuracy
  - Anthropic (Claude) — excellent for long documents
  - Google (Gemini) — good for multilingual Indian docs
  - Ollama (local) — for offline / air-gapped environments

The extraction prompt is specifically crafted for Indian health insurance
plans and maps to NRCeS NHCX FHIR InsurancePlan fields.
"""

import json
import logging
import re
from typing import Optional, List

from app.config import settings
from app.api.models import ExtractedInsurancePlan

logger = logging.getLogger("nhcx-converter.llm")

# ═══════════════════════════════════════════════════════════════════════════
# Extraction prompt — Indian health insurance domain-specific
# ═══════════════════════════════════════════════════════════════════════════

EXTRACTION_SYSTEM_PROMPT = """You are an expert Indian health insurance analyst with deep knowledge of:
- IRDAI regulations and health insurance product structures
- Indian insurance terminology (sum insured, sub-limits, co-pay, room rent, PED, AYUSH, etc.)
- NHCX and FHIR standards for insurance plan representation
- Common plan structures from insurers like Star Health, ICICI Lombard, Niva Bupa, Care Health, HDFC Ergo, etc.

Your task is to extract COMPLETE structured data from an insurance plan PDF text.
You must be thorough — missing a sub-limit or exclusion could affect claims processing.

Return ONLY valid JSON matching the schema below. No markdown, no explanation.
Do NOT wrap the JSON in ```json``` code fences. Return raw JSON only."""

EXTRACTION_USER_PROMPT = """Extract ALL insurance plan details from the following document text.
Be exhaustive — capture every benefit, sub-limit, exclusion, waiting period, and condition.

For Indian health insurance plans, pay special attention to:
1. Sum Insured variants and premium tables
2. Room rent limits (% of SI or fixed amount)
3. Co-payment clauses (age-based, disease-based)
4. Sub-limits on specific treatments (cataract, knee replacement, etc.)
5. AYUSH treatment coverage
6. Day Care procedures list
7. Maternity and newborn coverage
8. Organ donor expenses
9. Pre and post hospitalization periods
10. Waiting periods: initial (30-day), PED (typically 24-48 months), specific disease
11. Permanent exclusions (cosmetic, dental unless accident, etc.)
12. Restoration / recharge benefit
13. Cumulative bonus / No Claim Bonus
14. Network hospital restrictions

Return JSON in this exact schema:
{{
  "plan_name": "string — official product name",
  "plan_aliases": ["alternate names"],
  "plan_identifier": "UIN or product code if found",
  "plan_type": "individual | family-floater | group | government | critical-illness | top-up | super-top-up | other",
  "status": "active",
  "insurer_name": "string — full legal entity name",
  "insurer_irdai_registration": "IRDAI reg number if found",
  "tpa_name": "TPA name if mentioned",
  "effective_from": "YYYY-MM-DD or null",
  "effective_to": "YYYY-MM-DD or null",
  "coverage_areas": ["India"],
  "coverages": [
    {{
      "coverage_type": "inpatient | daycare | outpatient | maternity | domiciliary | organ-donor | ayush | other",
      "description": "brief description",
      "benefits": [
        {{
          "name": "Benefit name",
          "benefit_type": "NHCX benefit code if known",
          "description": "detailed description",
          "covered": true,
          "limits": [
            {{
              "description": "what this limit is for",
              "value": 50000,
              "percentage": null,
              "unit": "INR | percent | days",
              "condition": "condition for this limit"
            }}
          ],
          "waiting_period_days": null,
          "conditions": ["condition strings"],
          "supporting_docs": ["required documents"]
        }}
      ],
      "conditions": ["coverage-level conditions"],
      "supporting_info_requirements": ["required docs for this coverage"]
    }}
  ],
  "plan_costs": [
    {{
      "sum_insured": 500000,
      "premium": null,
      "copay_percentage": null,
      "deductible": null,
      "age_band": "18-35",
      "plan_variant": "Silver"
    }}
  ],
  "exclusions": [
    {{
      "name": "exclusion name",
      "description": "details",
      "exclusion_type": "permanent | waiting-period | conditional",
      "waiting_period_days": null
    }}
  ],
  "initial_waiting_period_days": 30,
  "pre_existing_disease_waiting_days": 1095,
  "specific_disease_waiting_days": 730,
  "contact_phone": null,
  "contact_email": null,
  "contact_website": null,
  "network_hospital_count": null,
  "extraction_confidence": 0.85,
  "extraction_warnings": ["any ambiguities or missing data notes"]
}}

DOCUMENT TEXT:
---
{document_text}
---

Return ONLY the JSON object. No other text. No markdown fences."""


class LLMProcessor:
    """Extracts structured insurance plan data using LLM."""

    def __init__(self):
        self.provider = settings.LLM_PROVIDER
        self.model = settings.LLM_MODEL
        self._llm = None

    def _get_llm(self):
        """Lazy-initialize the LLM based on provider."""
        if self._llm is not None:
            return self._llm

        if self.provider == "openai":
            from langchain_openai import ChatOpenAI
            self._llm = ChatOpenAI(
                model=self.model,
                temperature=settings.LLM_TEMPERATURE,
                max_tokens=settings.LLM_MAX_TOKENS,
                api_key=settings.LLM_API_KEY,
            )

        elif self.provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            self._llm = ChatAnthropic(
                model=self.model,
                temperature=settings.LLM_TEMPERATURE,
                max_tokens=settings.LLM_MAX_TOKENS,
                api_key=settings.LLM_API_KEY,
            )

        elif self.provider == "google":
            from langchain_google_genai import ChatGoogleGenerativeAI
            self._llm = ChatGoogleGenerativeAI(
                model=self.model,
                temperature=settings.LLM_TEMPERATURE,
                max_output_tokens=settings.LLM_MAX_TOKENS,
                google_api_key=settings.LLM_API_KEY,
            )

        elif self.provider == "ollama":
            from langchain_ollama import ChatOllama
            self._llm = ChatOllama(
                model=self.model,
                temperature=settings.LLM_TEMPERATURE,
                base_url=settings.OLLAMA_BASE_URL,
            )

        else:
            raise ValueError(f"Unsupported LLM provider: {self.provider}")

        return self._llm

    def _get_max_input_chars(self) -> int:
        """
        Return max input characters based on provider.
        Cloud APIs can handle 100K+ chars; local Ollama needs truncation.
        """
        if self.provider == "ollama":
            return 8000  # Local 8B models have small effective context
        elif self.provider == "google":
            return 900000  # Gemini supports 1M tokens
        elif self.provider == "openai":
            return 400000  # GPT-4o/mini: 128K tokens
        elif self.provider == "anthropic":
            return 600000  # Claude: 200K tokens
        return 100000  # Safe default

    async def extract_plan_data(
        self,
        document_text: str,
        chunks: Optional[List[str]] = None,
        source_filename: Optional[str] = None,
    ) -> ExtractedInsurancePlan:
        """
        Extract structured insurance plan data from document text.

        Strategy:
        - Cloud APIs (OpenAI, Gemini, Anthropic): Send FULL document
        - Ollama (local): Truncate to 8K chars (small context window)
        - Very large documents (>max chars): Chunked extraction + merge
        """
        from langchain_core.messages import SystemMessage, HumanMessage

        llm = self._get_llm()
        max_chars = self._get_max_input_chars()

        logger.info(
            "Provider=%s, Model=%s, Doc length=%d chars, Max input=%d chars",
            self.provider, self.model, len(document_text), max_chars,
        )

        # Truncate ONLY if document exceeds provider's context limit
        if len(document_text) > max_chars:
            if self.provider == "ollama":
                # For local models, just truncate (no chunking — too slow)
                logger.info("Ollama: truncating from %d to %d chars", len(document_text), max_chars)
                document_text = document_text[:max_chars] + "\n\n[DOCUMENT TRUNCATED]"
                result = await self._extract_single(llm, document_text)
            else:
                # For cloud APIs, use chunked extraction
                logger.info("Document exceeds limit, using chunked extraction")
                if chunks is None:
                    from app.services.pdf_extractor import PDFExtractor
                    extractor = PDFExtractor()
                    chunk_size = min(settings.CHUNK_SIZE_CHARS, max_chars)
                    chunks = extractor.chunk_text(document_text, chunk_size=chunk_size)
                result = await self._extract_chunked(llm, chunks)
        else:
            # Document fits — send the FULL text in one shot
            logger.info("Single-pass extraction: sending ALL %d chars to LLM", len(document_text))
            result = await self._extract_single(llm, document_text)

        # Set source filename
        if source_filename:
            result.source_pdf_filename = source_filename

        logger.info(
            "LLM extraction complete: plan='%s', insurer='%s', "
            "coverages=%d, exclusions=%d, confidence=%.2f",
            result.plan_name,
            result.insurer_name,
            len(result.coverages),
            len(result.exclusions),
            result.extraction_confidence or 0,
        )

        return result

    async def _extract_single(self, llm, text: str) -> ExtractedInsurancePlan:
        """Single-pass extraction — sends text to the LLM."""
        from langchain_core.messages import SystemMessage, HumanMessage

        logger.info("Sending %d chars to LLM (%s / %s)...", len(text), self.provider, self.model)

        messages = [
            SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
            HumanMessage(content=EXTRACTION_USER_PROMPT.format(document_text=text)),
        ]

        try:
            response = await llm.ainvoke(messages)
            response_text = response.content
            logger.info("LLM responded with %d chars", len(response_text))
        except Exception as e:
            logger.error("LLM invoke failed: %s: %s", type(e).__name__, e)
            raise ValueError(f"LLM call failed: {type(e).__name__}: {e}")

        # Save raw response for debugging
        try:
            import os
            os.makedirs("data/output", exist_ok=True)
            with open("data/output/last_llm_response.txt", "w", encoding="utf-8") as f:
                f.write("=== RAW LLM RESPONSE ===\n")
                f.write(response_text)
                f.write("\n\n=== END ===\n")
            logger.info("Raw LLM response saved to data/output/last_llm_response.txt")
        except Exception as e:
            logger.warning("Could not save debug file: %s", e)

        return self._parse_response(response_text)

    async def _extract_chunked(
        self, llm, chunks: List[str]
    ) -> ExtractedInsurancePlan:
        """
        Multi-chunk extraction for very large documents.
        Extracts from each chunk, then uses LLM to merge results.
        """
        from langchain_core.messages import SystemMessage, HumanMessage

        logger.info("Processing %d chunks", len(chunks))
        partial_results = []

        for i, chunk in enumerate(chunks):
            logger.info("Processing chunk %d/%d (%d chars)", i + 1, len(chunks), len(chunk))
            messages = [
                SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
                HumanMessage(content=EXTRACTION_USER_PROMPT.format(
                    document_text=f"[CHUNK {i+1} OF {len(chunks)}]\n{chunk}"
                )),
            ]
            try:
                response = await llm.ainvoke(messages)
                partial = json.loads(self._clean_json(response.content))
                partial_results.append(partial)
                logger.info("Chunk %d: extracted plan='%s'", i + 1, partial.get("plan_name", "?"))
            except json.JSONDecodeError:
                logger.warning("Chunk %d: Failed to parse JSON", i + 1)
            except Exception as e:
                logger.warning("Chunk %d: LLM call failed: %s", i + 1, e)

        if not partial_results:
            raise ValueError("All chunks failed to produce valid JSON")

        # If only one chunk succeeded, use it directly
        if len(partial_results) == 1:
            return ExtractedInsurancePlan(**partial_results[0])

        # Merge partial results using LLM
        logger.info("Merging %d partial extractions", len(partial_results))
        merge_prompt = f"""You have {len(partial_results)} partial extractions from
different sections of the same insurance plan PDF. Merge them into one
complete JSON object. Remove duplicates, resolve conflicts by keeping
the most detailed version. Return ONLY valid JSON, no markdown fences.

PARTIAL EXTRACTIONS:
{json.dumps(partial_results, indent=2)}

Return the merged JSON:"""

        messages = [
            SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
            HumanMessage(content=merge_prompt),
        ]
        response = await llm.ainvoke(messages)
        return self._parse_response(response.content)

    def _parse_response(self, content: str) -> ExtractedInsurancePlan:
        """Parse LLM response into ExtractedInsurancePlan."""
        cleaned = self._clean_json(content)

        logger.info("Cleaned JSON length: %d chars", len(cleaned))
        logger.info("First 200 chars: %s", cleaned[:200])

        try:
            data = json.loads(cleaned)
            return ExtractedInsurancePlan(**data)
        except json.JSONDecodeError as e:
            logger.error("JSON parse failed: %s", e)
            logger.error("Cleaned (first 500 chars): %s", cleaned[:500])
            raise ValueError(
                f"LLM returned invalid JSON: {e}. "
                "Check data/output/last_llm_response.txt for the raw output."
            )
        except Exception as e:
            logger.error("Data validation failed: %s", e)
            raise ValueError(f"Extracted data validation failed: {e}")

    def _clean_json(self, text: str) -> str:
        """Strip markdown fences and extract JSON from LLM output."""
        text = text.strip()

        # Remove markdown code fences
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # Find the actual JSON object — LLMs sometimes add text before/after
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

        return text.strip()
