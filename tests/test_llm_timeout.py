def test_every_llm_has_a_timeout_and_bounded_retries():
    from app import llm

    for name in ("chat_llm", "research_llm", "knowledge_llm", "analysis_llm"):
        model = getattr(llm, name)
        assert model.request_timeout is not None, f"{name} has no timeout"
        assert model.max_retries <= 2, f"{name} retries too many times"
