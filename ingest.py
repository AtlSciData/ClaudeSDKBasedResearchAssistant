"""
Chunk the public AIG PDFs into overlapping text windows and save them
as a flat JSON index. Run this once (and again whenever data/raw/ changes).
"""
import json
import re
from pathlib import Path
from pypdf import PdfReader

RAW_DIR = Path("data/raw")
INDEX_PATH = Path("data/index/chunks.json")

CHUNK_SIZE = 900       # characters
CHUNK_OVERLAP = 150


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def chunk_page(text: str, doc_id: str, page_num: int) -> list[dict]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunk_text = text[start:end]
        if chunk_text.strip():
            chunks.append({
                "id": f"{doc_id}:p{page_num}:{start}",
                "doc_id": doc_id,
                "page": page_num,
                "text": chunk_text,
            })
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def main():
    all_chunks = []
    doc_manifest = []

    pdf_paths = sorted(RAW_DIR.glob("*.pdf"))
    if not pdf_paths:
        raise SystemExit(f"No PDFs found in {RAW_DIR.resolve()} — add some first.")

    for pdf_path in pdf_paths:
        doc_id = pdf_path.stem
        reader = PdfReader(str(pdf_path))
        num_pages = len(reader.pages)
        doc_manifest.append({"doc_id": doc_id, "title": doc_id, "num_pages": num_pages})

        for page_num, page in enumerate(reader.pages, start=1):
            raw_text = page.extract_text() or ""
            text = clean_text(raw_text)
            if text:
                all_chunks.extend(chunk_page(text, doc_id, page_num))

        print(f"Ingested {pdf_path.name}: {num_pages} pages")

    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(
        {"documents": doc_manifest, "chunks": all_chunks}, indent=2
    ))
    print(f"\nWrote {len(all_chunks)} chunks from {len(doc_manifest)} documents to {INDEX_PATH}")


if __name__ == "__main__":
    main()