import json

from podcast_agent.tools import run_tool, word_count

SUMMARY_220 = " ".join(["word"] * 220)


def test_summary_word_limits(state):
    content, err = run_tool(state, "submit_summary", {"summary": "too short"})
    assert err and "2 words" in content
    content, err = run_tool(state, "submit_summary", {"summary": SUMMARY_220})
    assert not err and state.summary


def _notes(**overrides):
    notes = {
        "takeaways": [f"Takeaway {i}" for i in range(5)],
        "quotes": [
            {"timestamp": "00:02:45", "speaker": "Mark", "quote": "tools aren't the solution. Culture is."},
            {"timestamp": "03:25", "speaker": "Mark Rivera", "quote": "Outcomes over hours."},
            {"timestamp": "00:04:10", "speaker": "Mark", "quote": "Remote doesn’t mean isolated; it just needs intention."},
        ],
        "topics": ["Async Communication", "documentation", "remote culture", "productivity"],
    }
    notes.update(overrides)
    return notes


def test_key_notes_accepts_verbatim_quotes(state):
    content, err = run_tool(state, "submit_key_notes", _notes())
    assert not err, content
    assert state.key_notes.quotes[0].speaker == "Mark Rivera"
    assert state.key_notes.quotes[1].timestamp == "00:03:25"
    assert "async-communication" in state.key_notes.topics


def test_key_notes_rejects_invented_quote_and_wrong_speaker(state):
    bad = _notes()
    bad["quotes"][0]["quote"] = "Slack is the root of all evil."
    bad["quotes"][1]["speaker"] = "Sarah"
    content, err = run_tool(state, "submit_key_notes", bad)
    assert err
    assert "Quote 1" in content and "does not appear verbatim" in content
    assert "Quote 2" in content and "Mark Rivera" in content
    assert state.key_notes is None


def test_key_notes_requires_exactly_five_takeaways(state):
    content, err = run_tool(state, "submit_key_notes", _notes(takeaways=["one", "two"]))
    assert err and "exactly 5 takeaways" in content


def _claim(**overrides):
    c = {
        "claim": "GitLab publishes a public company handbook.",
        "timestamp": "00:01:20",
        "speaker": "Mark",
        "claim_type": "entity_fact",
        "status": "verified",
        "confidence": 0.93,
        "verification": "True (GitLab handbook)",
        "evidence": "GitLab's handbook is public.",
        "counter_evidence": "none",
        "sources": ["kb-remote-001"],
    }
    c.update(overrides)
    return c


def test_fact_checks_require_kb_search_first(state):
    content, err = run_tool(state, "submit_fact_checks", {"fact_checks": [_claim()] * 5})
    assert err and "Search the knowledge base" in content


def test_fact_checks_guardrails(state):
    run_tool(state, "search_knowledge_base", {"query": "GitLab handbook"})
    claims = [
        _claim(),
        _claim(claim_type="prediction", status="verified", timestamp="00:05:00"),
        _claim(status="possibly_inaccurate", sources=[]),
        _claim(sources=["kb-does-not-exist"]),
        _claim(counter_evidence="IDx-DR was authorised as an autonomous diagnostic in 2018"),
    ]
    content, err = run_tool(state, "submit_fact_checks", {"fact_checks": claims})
    assert err
    assert "Claim 2: a prediction cannot be 'verified'" in content
    assert "Claim 3" in content and "needs at least one source" in content
    assert "Claim 4: unknown knowledge base ids" in content
    assert "Claim 5: you recorded counter-evidence" in content

    ok = [_claim(), _claim(claim_type="prediction", status="unverifiable", confidence=0.4, sources=[], timestamp="00:05:00")] * 2
    ok.append(_claim(status="possibly_inaccurate", counter_evidence="Automattic's handbook is internal"))
    content, err = run_tool(state, "submit_fact_checks", {"fact_checks": ok})
    assert not err, content
    assert json.loads(content)["by_status"] == {"verified": 2, "unverifiable": 2, "possibly_inaccurate": 1}


def test_finish_requires_all_artifacts(state):
    content, err = run_tool(state, "finish", {"notes": ""})
    assert err and "submit_summary" in content


def test_word_count_handles_apostrophes():
    assert word_count("It's async-first, isn't it?") == 4
