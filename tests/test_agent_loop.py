"""Drives the agent loop with a scripted fake client: no network, no API key."""

from types import SimpleNamespace as NS

from rich.console import Console

from podcast_agent.agent import run_episode
from podcast_agent.trace import Tracer

from test_tools import SUMMARY_220, _claim, _notes


def text(t):
    return NS(type="text", text=t)


def tool(i, name, args):
    return NS(type="tool_use", id=f"tu_{i}", name=name, input=args)


def resp(*blocks, stop="tool_use"):
    usage = NS(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0, server_tool_use=None)
    return NS(content=list(blocks), stop_reason=stop, usage=usage)


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.messages = NS(create=self._create)
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.script.pop(0)


def test_agent_recovers_from_rejection_and_finishes(state, tmp_path):
    script = [
        resp(text("1. Summarise\n2. Notes\n3. Check GitLab claim"), tool(1, "search_knowledge_base", {"query": "GitLab handbook"})),
        resp(text("Submitting summary."), tool(2, "submit_summary", {"summary": "short"})),
        resp(
            text("Expanding the summary."),
            tool(3, "submit_summary", {"summary": SUMMARY_220}),
            tool(4, "submit_key_notes", _notes()),
            tool(5, "submit_fact_checks", {"fact_checks": [_claim()] * 5}),
        ),
        resp(tool(6, "finish", {"notes": "none"})),
    ]
    client = FakeClient(script)
    tracer = Tracer("ep001", tmp_path, Console(file=open(tmp_path / "console.txt", "w")))
    report = run_episode(client, state, tracer)
    tracer.close()

    assert report.run.completed
    assert report.run.agent_turns == 4
    assert report.run.tool_calls == 6
    assert report.run.validation_rejections == 1
    assert report.run.plan.startswith("1. Summarise")
    # The rejection was sent back to the model as an error tool_result.
    second_reply = client.requests[2]["messages"][-1]["content"][0]
    assert second_reply["is_error"] and "must be 200-300" in second_reply["content"]
    # Every request used the configured model and adaptive thinking.
    assert all(r["model"] == "z-ai/glm-5.3-flash" and r["thinking"]["type"] == "adaptive" for r in client.requests)
    assert all(r["output_config"] == {"effort": "high"} for r in client.requests)
    log = (tmp_path / "run.log").read_text()
    assert "PLAN" in log and "REJECT" in log and "DONE" in log


def test_agent_nudges_when_it_stops_early(state, tmp_path):
    script = [resp(text("I think I'm done."), stop="end_turn")] * 2
    state.settings.agent.max_turns = 2
    client = FakeClient(script)
    tracer = Tracer("ep001", tmp_path, Console(file=open(tmp_path / "console.txt", "w")))
    report = run_episode(client, state, tracer)
    tracer.close()
    assert not report.run.completed
    assert "Still needed" in client.requests[1]["messages"][-1]["content"]
