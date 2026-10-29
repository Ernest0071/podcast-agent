from podcast_agent.ingest import clean_text, load_episode, normalise_timestamp, resolve_speaker
from conftest import ROOT


def test_normalise_timestamp():
    assert normalise_timestamp("01:20") == "00:01:20"
    assert normalise_timestamp("00:02:14") == "00:02:14"
    assert normalise_timestamp("83") == "00:01:23"


def test_clean_text_removes_fillers_and_stutters():
    assert clean_text("So um, the the mission is uh launching") == "So the mission is launching"
    assert clean_text("I like it, you know") == "I like it, you know"


def test_resolve_speaker():
    people = ["David Chen", "Dr. Priya Patel", "Alex Moore"]
    assert resolve_speaker("Dr. Priya", people) == "Dr. Priya Patel"
    assert resolve_speaker("Alex", people) == "Alex Moore"
    assert resolve_speaker("Narrator", people) == "Narrator"


def test_load_all_sample_transcripts():
    for path in sorted((ROOT / "data" / "transcripts").glob("*.json")):
        ep = load_episode(path)
        assert ep.segments and ep.title
        assert all(len(s.timestamp) == 8 for s in ep.segments)


def test_load_txt(tmp_path):
    p = tmp_path / "ep009_mars.txt"
    p.write_text(
        "[00:02:14] Host: So NASA recently announced something about a new Mars mission.\n"
        "[00:02:18] Guest: Yes, it's launching in early 2026 —\n"
        "they're planning to bring back samples.\n"
        "[00:03:01] Host: Wow, that's exciting.\n",
        encoding="utf-8",
    )
    ep = load_episode(p)
    assert ep.episode_id == "ep009"
    assert len(ep.segments) == 3
    assert ep.segments[1].text.endswith("bring back samples.")
