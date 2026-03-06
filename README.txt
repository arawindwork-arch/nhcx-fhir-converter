================================================================================
  NHCX Insurance Plan FHIR Bundle Converter
  Problem Statement 03: PDF → NHCX-aligned Insurance Plan FHIR Bundle
  NHCX Hackathon 2026 | Ayushman Bharat Digital Mission (ABDM)
================================================================================

1. BRIEF FUNCTIONAL SCOPE
================================================================================

This open-source microservice automates the conversion of Indian health 
insurance plan PDFs into NHCX-compliant FHIR R4 InsurancePlan bundles.

What it solves:
  Today, converting a single insurance plan PDF (e.g., Star Health Family
  Health Optima with 40+ pages of benefits, sub-limits, exclusions) into
  an NHCX-compliant FHIR bundle requires a skilled FHIR developer spending
  2-3 days of manual work. With 30+ Indian insurers, each having 10-50 plan
  variants, this creates a bottleneck of 500+ plans needing conversion.

  This tool reduces that from days to minutes per plan.

Pipeline:
  1. PDF Upload → Text + Table Extraction (pdfplumber + OCR fallback)
  2. LLM-Powered Structured Extraction (benefits, limits, exclusions, costs)
  3. FHIR R4 InsurancePlan Resource Mapping (NRCeS v6.5.0 profile)
  4. InsurancePlanBundle Assembly (with Organization resources)
  5. NRCeS NHCX Profile Validation (required/optional field checks)
  6. Optional Human Review & Correction (before final bundle generation)
  7. Output: FHIR JSON bundle + FHIR Mapping Excel

Key capabilities:
  - Handles both digital and scanned (OCR) PDFs
  - Supports Hindi + English bilingual documents
  - Configuration-driven mappings for insurer-specific terminology
  - Covers all Indian health insurance constructs:
    * Sub-limits, co-pay, room rent caps, deductibles
    * AYUSH coverage, maternity, organ donor, day care
    * PED waiting periods, specific disease waiting periods
    * Restoration/recharge benefits, cumulative bonus
    * 13+ permanent exclusion categories (IRDAI standard)
  - Validates against NRCeS NHCX InsurancePlan StructureDefinition
  - Supports NHCX extensions: Claim-Exclusion, Claim-Condition,
    Claim-SupportingInfoRequirement


2. HIGH-LEVEL ARCHITECTURE
================================================================================

  ┌─────────────┐    ┌──────────────────┐    ┌─────────────────┐
  │  Insurance   │    │   PDF Extractor   │    │  LLM Processor  │
  │  Plan PDF    │───>│  (pdfplumber +    │───>│  (LangChain +   │
  │  Upload      │    │   OCR fallback)   │    │   GPT-4o/Claude)│
  └─────────────┘    └──────────────────┘    └────────┬────────┘
                                                       │
                                                       ▼
  ┌─────────────┐    ┌──────────────────┐    ┌─────────────────┐
  │  FHIR JSON  │    │   NRCeS NHCX     │    │   FHIR Mapper   │
  │  Bundle +   │<───│   Validator      │<───│  (InsurancePlan  │
  │  Mapping XL │    │  (Profile checks) │    │   + extensions)  │
  └─────────────┘    └──────────────────┘    └─────────────────┘
                              │
                     ┌────────▼────────┐
                     │  Human Review   │  (Optional)
                     │  Correction UI  │
                     └─────────────────┘

  API Layer: FastAPI (REST endpoints)
  Data Flow: Async pipeline with job tracking

  Endpoints:
    POST /api/v1/convert       Upload PDF → get FHIR bundle
    GET  /api/v1/jobs/{id}     Check conversion job status
    POST /api/v1/review/{id}   Submit human corrections
    POST /api/v1/validate      Validate existing FHIR bundle
    GET  /api/v1/download/{id} Download output bundle
    GET  /api/v1/mappings      List mapping configurations
    GET  /health               Service health check
    GET  /docs                 Interactive Swagger UI


3. TOOLS AND LIBRARIES USED
================================================================================

  OPEN SOURCE:
  ──────────────────────────────────────────────────────────────────────────
  Framework:
    - FastAPI 0.115.6         — Async REST API framework
    - Uvicorn 0.34.0          — ASGI server
    - Pydantic 2.10.4         — Data validation & serialization

  PDF Processing:
    - pdfplumber 0.11.4       — Text & table extraction from PDFs
    - PyPDF2 3.0.1            — PDF metadata reading
    - pdf2image (optional)    — PDF to image for OCR
    - pytesseract (optional)  — OCR for scanned PDFs

  LLM / AI:
    - LangChain Core 0.3.28   — LLM orchestration framework
    - langchain-openai 0.3.0  — OpenAI integration (GPT-4o)

  Data / Output:
    - openpyxl 3.1.5          — Excel file generation
    - PyYAML 6.0.2            — YAML config parsing
    - python-dotenv 1.0.1     — Environment management

  Containerization:
    - Docker                  — Container runtime
    - Docker Compose          — Multi-service orchestration

  CLOSED SOURCE:
  ──────────────────────────────────────────────────────────────────────────
    - OpenAI GPT-4o API       — LLM for structured data extraction
      (Alternative: Anthropic Claude, Google Gemini, or Ollama for
       fully local/offline operation)

  FHIR STANDARDS REFERENCED:
  ──────────────────────────────────────────────────────────────────────────
    - HL7 FHIR R4.0.1
    - NRCeS FHIR IG for ABDM v6.5.0
    - NHCX InsurancePlan Profile
    - NHCX InsurancePlanBundle Profile
    - NRCeS Extensions: Claim-Exclusion, Claim-Condition,
      Claim-SupportingInfoRequirement
    - NRCeS Value Sets: ndhm-insuranceplan-type, ndhm-coverage-type,
      ndhm-benefit-type, ndhm-plan-type, ndhm-benefitcategory,
      ndhm-productorservice


4. SETUP INSTRUCTIONS
================================================================================

  OPTION A: Docker (Recommended)
  ──────────────────────────────────────────────────────────────────────────

    # 1. Clone the repository
    git clone <repo-url>
    cd nhcx-insurance-plan-converter

    # 2. Configure environment
    cp .env.example .env
    # Edit .env and add your LLM_API_KEY

    # 3. Build and run
    docker-compose up --build

    # 4. Access the service
    # API:     http://localhost:8000
    # Swagger: http://localhost:8000/docs
    # Health:  http://localhost:8000/health

  OPTION B: Local Python
  ──────────────────────────────────────────────────────────────────────────

    # 1. Prerequisites
    Python 3.10+ required

    # 2. Create virtual environment
    python -m venv venv
    source venv/bin/activate  # Linux/Mac
    # venv\Scripts\activate   # Windows

    # 3. Install dependencies
    pip install -r requirements.txt

    # 4. Configure environment
    cp .env.example .env
    # Edit .env and add your LLM_API_KEY

    # 5. Run the service
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

  OPTION C: Fully Offline (Ollama)
  ──────────────────────────────────────────────────────────────────────────

    # 1. Install Ollama: https://ollama.ai
    ollama pull llama3.1

    # 2. Configure .env
    LLM_PROVIDER=ollama
    LLM_MODEL=llama3.1
    OLLAMA_BASE_URL=http://localhost:11434

    # 3. Run as in Option B

  For OCR support (scanned PDFs):
    sudo apt-get install tesseract-ocr tesseract-ocr-hin poppler-utils
    pip install pdf2image pytesseract


5. DEPENDENCIES
================================================================================

  System:
    - Python >= 3.10
    - Docker (optional, for containerized deployment)
    - Tesseract OCR (optional, for scanned PDFs)
    - Poppler utils (optional, for pdf2image)

  Python packages: See requirements.txt

  External APIs:
    - OpenAI API key (or Anthropic/Google API key, or Ollama for offline)


6. IMPLEMENTATION DETAILS
================================================================================

  A. PDF TEXT EXTRACTION (app/services/pdf_extractor.py)
  ──────────────────────────────────────────────────────────────────────────
  - Primary: pdfplumber — handles text-based PDFs with table detection
  - Fallback: pytesseract OCR — for scanned/image PDFs
  - Supports Hindi+English bilingual documents (tesseract-ocr-hin)
  - Table extraction preserves benefit/limit structure
  - Smart chunking with paragraph-aware boundaries (4000 chars, 500 overlap)
  - Quality scoring: high (>500 chars/page), medium, low

  B. LLM STRUCTURED EXTRACTION (app/services/llm_processor.py)
  ──────────────────────────────────────────────────────────────────────────
  - Domain-specific prompt engineered for Indian health insurance:
    * Knows IRDAI terminology (UIN, sub-limits, PED, AYUSH, etc.)
    * Handles 13+ coverage types and 15+ benefit categories
    * Extracts all exclusions (permanent + waiting-period-based)
    * Captures premium tables, age bands, plan variants
  - Multi-LLM support: OpenAI GPT-4o, Anthropic Claude, Google Gemini, Ollama
  - Chunked processing for large documents (>30K chars)
  - Automatic merging of partial chunk results
  - Low temperature (0.1) for deterministic extraction
  - JSON output validated against Pydantic schema

  C. FHIR R4 MAPPING (app/services/fhir_mapper.py)
  ──────────────────────────────────────────────────────────────────────────
  - Generates InsurancePlanBundle (Bundle type=collection)
  - Resources in bundle:
    * InsurancePlan — main resource with all plan details
    * Organization (insurer) — with IRDAI registration
    * Organization (TPA) — if applicable
  - NRCeS profile compliance:
    * All required fields populated (identifier, status, type, name, period, ownedBy)
    * NRCeS value sets: insuranceplan-type, coverage-type, benefit-type, plan-type
    * Extensions: Claim-Exclusion, Claim-Condition, Claim-SupportingInfoRequirement
  - Coverage types mapped: Inpatient, Daycare, Outpatient, Maternity,
    Domiciliary, Organ Donor, AYUSH
  - Plan cost details: sum insured, premium, co-pay, deductible

  D. NRCeS PROFILE VALIDATION (app/services/validator.py)
  ──────────────────────────────────────────────────────────────────────────
  - Bundle structure validation (type=collection, correct profile)
  - InsurancePlan cardinality checks per NRCeS v6.5.0:
    * identifier (1..1), status (1..1), type (1..1), name (1..1)
    * period (1..1), ownedBy (1..1), coverage (1..*), benefit (1..*)
  - Value set binding validation
  - Coding structure completeness (system + code + display)
  - Extension structure validation
  - Reference integrity (all references resolve within bundle)
  - ipn-1 constraint check
  - Returns errors, warnings, and informational issues

  E. CONFIGURATION-DRIVEN MAPPINGS (app/mappings/)
  ──────────────────────────────────────────────────────────────────────────
  - Default mapping covers standard Indian insurance terminology
  - Insurer-specific overrides via YAML config files
  - Pre-loaded knowledge of 11 major Indian insurers with IRDAI reg numbers
  - Terminology aliases for plan types, coverage types, benefit names
  - Common permanent exclusions (13+ IRDAI standard exclusions)
  - Standard waiting periods (30-day initial, 36-month PED, 24-month specific)

  F. HUMAN REVIEW WORKFLOW
  ──────────────────────────────────────────────────────────────────────────
  - Extracted data saved as JSON for review before FHIR generation
  - POST /api/v1/review/{job_id} endpoint to submit corrections
  - Reviewer can modify any extracted field
  - Re-runs FHIR mapping and validation after corrections
  - Can be bypassed with skip_review=True for automated pipelines

  G. OUTPUT GENERATION
  ──────────────────────────────────────────────────────────────────────────
  - FHIR InsurancePlan Bundle (JSON)
  - FHIR Mapping Excel (field-by-field PDF→FHIR mapping with NRCeS profiles)
  - Both files follow hackathon naming conventions


7. DIRECTORY STRUCTURE
================================================================================

  nhcx-insurance-plan-converter/
  ├── app/
  │   ├── main.py                    # FastAPI application
  │   ├── config.py                  # Settings (env vars)
  │   ├── api/
  │   │   ├── routes.py              # API endpoints
  │   │   └── models.py              # Pydantic data models
  │   ├── services/
  │   │   ├── pdf_extractor.py       # PDF text extraction
  │   │   ├── llm_processor.py       # LLM structured extraction
  │   │   ├── fhir_mapper.py         # FHIR R4 mapping
  │   │   ├── validator.py           # NRCeS profile validation
  │   │   └── pipeline.py            # Pipeline orchestrator
  │   ├── fhir/
  │   │   ├── profiles/              # NRCeS NHCX profiles
  │   │   └── templates/             # FHIR resource templates
  │   └── mappings/
  │       └── default_mapping.yaml   # Terminology mappings
  ├── data/
  │   ├── uploads/                   # Uploaded PDFs
  │   ├── output/                    # Generated FHIR bundles
  │   └── review/                    # Pending human review
  ├── tests/                         # Test suite
  ├── docs/                          # Documentation
  ├── Dockerfile
  ├── docker-compose.yml
  ├── requirements.txt
  ├── .env.example
  └── README.txt


8. USAGE EXAMPLES
================================================================================

  # Convert a PDF to FHIR bundle (with human review)
  curl -X POST http://localhost:8000/api/v1/convert \
    -F "file=@star_health_family_optima.pdf"

  # Convert with auto-completion (skip review)
  curl -X POST http://localhost:8000/api/v1/convert \
    -F "file=@icici_lombard_complete_health.pdf" \
    -F "skip_review=true" \
    -F "auto_validate=true"

  # Check job status
  curl http://localhost:8000/api/v1/jobs/{job_id}

  # Download output bundle
  curl http://localhost:8000/api/v1/download/{job_id} -o bundle.json

  # Validate an existing bundle
  curl -X POST http://localhost:8000/api/v1/validate \
    -H "Content-Type: application/json" \
    -d @existing_bundle.json

  Interactive API docs: http://localhost:8000/docs


================================================================================
  Built for NHCX Hackathon 2026 — Simplifying the claim journey for India
  Ayushman Bharat Digital Mission (ABDM) | National Health Authority (NHA)
================================================================================
