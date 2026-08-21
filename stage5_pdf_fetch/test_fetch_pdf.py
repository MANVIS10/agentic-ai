"""
Simple, dependency-free test for the fetch_pdf tool.

Not a pytest suite (the project has none configured yet) - just a script
that calls the tool directly against a real PDF URL and asserts the
returned text looks like readable content, not garbled binary.

Run with:
    python stage5_pdf_fetch/test_fetch_pdf.py
"""

from main import fetch_pdf

PDF_URL = "https://www.ijtsrd.com/papers/ijtsrd49820.pdf"


def run():
    result = fetch_pdf.invoke({"url": PDF_URL})
    print(f"URL: {PDF_URL}")
    print(f"Result length: {len(result)} chars")
    print(f"Preview: {result[:300].encode('ascii', 'replace').decode()}\n{'-' * 60}")

    assert len(result) > 200, "Expected a substantial amount of extracted text."
    assert "Failed to" not in result, "Expected successful extraction, got an error."
    assert "%PDF" not in result, "Result looks like raw PDF binary, not extracted text."

    bad_result = fetch_pdf.invoke({"url": "https://this-domain-does-not-exist-abc123.example/file.pdf"})
    assert "Failed to fetch" in bad_result, "Expected a friendly error for an unreachable URL."
    print(f"Bad URL result: {bad_result}\n{'-' * 60}")

    print("All checks passed: fetch_pdf returns readable text and handles errors.")


if __name__ == "__main__":
    run()
