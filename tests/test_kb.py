def test_search_ranks_relevant_entry_first(kb):
    hits = kb.search("FDA autonomous AI diagnostics IDx-DR")
    assert hits[0]["id"] == "kb-health-002"
    assert hits[0]["relevance"] == 1.0


def test_search_no_match(kb):
    assert kb.search("zzzz qqqq") == []
