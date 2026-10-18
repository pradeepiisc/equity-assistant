# Comprehensive Guide: Extracting Insights from Financial PDFs

This guide explains how to programmatically read and extract financial insights, management guidance, and growth drivers from Earnings Transcripts and Investor Presentations (PPTs). You can use this methodology to build independent, offline extraction pipelines without relying on expensive LLM API calls.

---

## 1. Prerequisites & Setup
The core library used for text extraction is `pdfplumber`, which is excellent for reading machine-generated PDFs (like official transcript documents and investor presentations).

**Installation:**
```bash
pip install pdfplumber
```

---

## 2. Locating the Files
In this workspace, company data is structured systematically. You should target the following directories for a given company (e.g., `AEROFLEX`):
- **Transcripts:** `data/companies/AEROFLEX/transcripts/`
- **Presentations:** `data/companies/AEROFLEX/ppts/`
- **Master Reports (Fallback):** `data/companies/AEROFLEX/reports/master_report.md`

**Best Practice:** Always sort the directory files by name/date descending to ensure you process the *latest* available quarter (e.g., `Mar 2026`, `Q4_FY26`).

---

## 3. Extracting Text from PDFs (Python)
Here is the robust, reusable snippet to safely extract text from a PDF file while handling potential exceptions (like encrypted or corrupted PDFs).

```python
import pdfplumber
import os

def extract_text_from_pdf(file_path):
    text = ""
    if not os.path.exists(file_path):
        return text
        
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    # Append with a space to avoid joining words across pages
                    text += page_text + " " 
    except Exception as e:
        print(f"Failed to read {file_path}: {e}")
        
    return text
```

---

## 4. Mining for "Insights" (Regex & Keyword Matching)
Once you have the raw text blob, you need to mine it for forward-looking statements. Management guidance typically revolves around specific keywords. 

### A. Define Your Keywords
```python
keywords = [
    'growth', 'revenue', 'margin', 'capacity', 'expansion', 
    'order', 'target', 'guidance', 'launch', 'demand', 'capex', 
    'EBITDA', 'FY27', 'FY28'
]
```

### B. Extract Sentences Containing Keywords
Split the massive text blob into sentences and filter them:
```python
import re

def extract_guidance_snippets(text, keywords):
    if not text:
        return []
        
    guidance_sentences = []
    # Split by periods to get sentences
    sentences = text.split('.')
    
    for sentence in sentences:
        # Check if any of our keywords exist in the sentence
        if any(kw.lower() in sentence.lower() for kw in keywords):
            clean_sentence = sentence.replace('\n', ' ').strip()
            
            # Heuristic: Good insights usually contain numbers (percentages, crores, years)
            # and are longer than 30 characters
            if len(clean_sentence) > 30 and re.search(r'\d', clean_sentence):
                guidance_sentences.append(clean_sentence + ".")
                
    return deduplicate_snippets(guidance_sentences)
```

### C. Deduplication
Transcripts often repeat the same numbers (e.g., in the opening remarks, then again in the Q&A). Deduplicate them:
```python
def deduplicate_snippets(snippets):
    unique_snippets = []
    seen = set()
    for s in snippets:
        # Normalize spaces for comparison
        clean = re.sub(r'\s+', ' ', s).strip()
        if clean not in seen:
            seen.add(clean)
            unique_snippets.append(clean)
    return unique_snippets
```

---

## 5. The Fallback Strategy
Transcripts might be delayed, missing, or scanned as images (making standard text extraction fail). You must build a fallback pipeline:

1. **Attempt Transcript:** Parse `transcripts/*.pdf`.
2. **Attempt PPT:** If the transcript yields 0 insights, parse the latest `ppts/*.pdf`. Presentations are highly dense in metrics.
3. **Attempt Master Report:** If both PDFs fail, parse `reports/master_report.md`. Look for bullet points (`-` or `*`) under sections like "Investment Thesis" or "Key Growth Drivers".

### Example of parsing a Master Report:
```python
def get_master_report_insights(symbol):
    path = f"data/companies/{symbol}/reports/master_report.md"
    insights = []
    if os.path.exists(path):
        with open(path, 'r', errors='ignore') as f:
            content = f.read()
            for line in content.split('\n'):
                line = line.strip()
                if line.startswith('- ') or line.startswith('* '):
                    if any(kw in line.lower() for kw in keywords):
                        # Remove bullet characters
                        clean_line = re.sub(r'^[-*]\s*', '', line).strip()
                        if len(clean_line) > 20:
                            insights.append(clean_line)
    return insights
```

---

## 6. Formatting the Final Report
Once you have an array of 4-5 high-quality string snippets, you can inject them directly into your markdown generators:

```python
snippets = extract_guidance_snippets(pdf_text, keywords)
if not snippets:
    snippets = get_master_report_insights(company_symbol)

# Output generation
report_lines = ["**Key Growth Drivers:**"]
for s in snippets[:5]: # Keep the top 5 to avoid bloating the report
    report_lines.append(f"- {s}")
```

## Advanced Tip: OCR for Scanned PDFs
If a company uploads a purely scanned image PDF, `pdfplumber` will return empty strings. If you need to upgrade this script in the future to handle scanned documents, install `pytesseract` and `pdf2image`. 

```bash
# Concept only
pip install pytesseract pdf2image
```
You would convert the PDF pages to images, then pass them through `pytesseract.image_to_string(image)`. However, for 95% of modern Indian corporate filings, `pdfplumber` is sufficient.
