"""
Simple, dependency-free test for the search_knowledge_base tool.

Not a pytest suite (the project has none configured yet) - just a script
that calls the tool directly and asserts it retrieves chunks from the
expected source document for a few sample queries.

Run with:
    python stage3_rag/test_search_knowledge_base.py
"""

from main import search_knowledge_base

CASES = [
    ("How do solar panels turn sunlight into electricity?", "solar.md"),
    ("What wind speed makes a turbine shut down?", "wind.md"),
    ("How can hydropower store energy for later?", "hydro.md"),
]


def run():
    for query, expected_source in CASES:
        result = search_knowledge_base.invoke({"query": query})
        print(f"Query: {query}")
        print(f"Result:\n{result}\n{'-' * 60}")

        assert expected_source in result, (
            f"Expected a chunk from '{expected_source}' for query "
            f"'{query}', but it wasn't in the results."
        )
        assert "[source:" in result, "Result is missing source metadata."

    print("All checks passed: search_knowledge_base retrieves relevant, "
          "sourced chunks.")


if __name__ == "__main__":
    run()
