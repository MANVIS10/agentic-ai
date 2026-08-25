"""
Simple, dependency-free test for the fetch_webpage tool.

Not a pytest suite (the project has none configured yet) - just a script
that calls the tool directly against a real, stable URL and asserts the
returned text looks like readable page content.

Run with:
    python stage4_web_fetch/test_fetch_webpage.py
"""

from main import fetch_webpage

CASES = [
    ("https://example.com", "Example Domain"),
]


def run():
    for url, expected_text in CASES:
        result = fetch_webpage.invoke({"url": url})
        print(f"URL: {url}")
        print(f"Result:\n{result}\n{'-' * 60}")

        assert expected_text in result, (
            f"Expected '{expected_text}' in the fetched text for {url}, "
            "but it wasn't there."
        )
        assert "<html" not in result.lower(), "Result still contains raw HTML tags."

    bad_result = fetch_webpage.invoke({"url": "https://this-domain-does-not-exist-abc123.example"})
    assert "Failed to fetch" in bad_result, "Expected a friendly error for an unreachable URL."
    print(f"Bad URL result:\n{bad_result}\n{'-' * 60}")

    print("All checks passed: fetch_webpage returns readable text and handles errors.")


if __name__ == "__main__":
    run()
