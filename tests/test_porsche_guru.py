import json
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from porsche_guru.agent import PorscheGuru
from porsche_guru.data import ModelTable, to_number

DATA = Path(__file__).resolve().parent / "fixtures" / "sample_models.csv"


class ModelTableTest(unittest.TestCase):
    def setUp(self):
        self.table = ModelTable(DATA)

    def test_to_number(self):
        self.assertEqual(to_number("$122,095"), 122095)
        self.assertEqual(to_number("3.9"), 3.9)
        self.assertIsNone(to_number("Coupe"))

    def test_numeric_and_text_filters(self):
        result = self.table.query(
            filters=[
                {"column": "base msrp (usd)", "op": "lt", "value": "$150,000"},
                {"column": "Body Style", "op": "eq", "value": "coupe"},
            ],
            columns=["Model"],
        )
        self.assertEqual([r["Model"] for r in result["rows"]], ["911 Carrera", "911 Carrera T"])

    def test_sort_and_limit(self):
        result = self.table.query(sort_by="Horsepower", descending=True, limit=2)
        self.assertEqual(result["total_matches"], 10)
        self.assertEqual([r["Model"] for r in result["rows"]], ["911 Turbo S", "911 Carrera GTS"])

    def test_unknown_column(self):
        with self.assertRaises(KeyError):
            self.table.query(filters=[{"column": "Color", "op": "eq", "value": "red"}])


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def fake_client(responses):
    messages = FakeMessages(responses)
    return NS(beta=NS(messages=messages)), messages


class AgentLoopTest(unittest.TestCase):
    def test_runs_table_tool_then_answers_with_sources(self):
        tool_call = NS(
            stop_reason="tool_use",
            content=[NS(type="tool_use", id="t1", name="query_model_table", input={
                "filters": [{"column": "Model", "op": "contains", "value": "GT3"}],
                "columns": ["Model", "Horsepower"], "sort_by": "", "descending": False, "limit": 5,
            })],
        )
        final = NS(stop_reason="end_turn", content=[
            NS(type="server_tool_use", name="web_search", input={"query": "992 GT3 for sale"}),
            NS(type="text", text="The GT3 makes 502 hp.", citations=[NS(url="https://bringatrailer.com/x", title="BaT")]),
        ])
        client, messages = fake_client([tool_call, final])
        activity = []
        guru = PorscheGuru(ModelTable(DATA), client=client, on_activity=activity.append)

        reply = guru.ask("Tell me about the GT3")

        self.assertEqual(reply.text, "The GT3 makes 502 hp.")
        self.assertEqual(reply.sources, [("BaT", "https://bringatrailer.com/x")])
        tool_result = messages.calls[1]["messages"][-1]["content"][0]
        self.assertEqual(json.loads(tool_result["content"])["rows"], [{"Model": "911 GT3", "Horsepower": "502"}])
        self.assertTrue(any("Searching the web" in a for a in activity))

    def test_pause_turn_resumes(self):
        paused = NS(stop_reason="pause_turn", content=[NS(type="server_tool_use", name="web_search", input={"query": "q"})])
        final = NS(stop_reason="end_turn", content=[NS(type="text", text="done", citations=None)])
        client, messages = fake_client([paused, final])
        guru = PorscheGuru(ModelTable(DATA), client=client)

        self.assertEqual(guru.ask("find listings").text, "done")
        self.assertEqual(len(messages.calls), 2)
        self.assertEqual(messages.calls[1]["messages"][-1]["role"], "assistant")

    def test_bad_tool_input_returns_error_result(self):
        bad = NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name="query_model_table", input={
            "filters": [{"column": "Nope", "op": "eq", "value": "x"}], "columns": [], "sort_by": "", "descending": False, "limit": 5,
        })])
        final = NS(stop_reason="end_turn", content=[NS(type="text", text="ok", citations=None)])
        client, messages = fake_client([bad, final])
        PorscheGuru(ModelTable(DATA), client=client).ask("q")
        self.assertTrue(messages.calls[1]["messages"][-1]["content"][0]["is_error"])

    def test_refusal_drops_turn(self):
        client, _ = fake_client([NS(stop_reason="refusal", content=[])])
        guru = PorscheGuru(ModelTable(DATA), client=client)
        guru.ask("q")
        self.assertEqual(guru.messages, [])


class DotenvTest(unittest.TestCase):
    def test_loads_values_without_overriding_shell(self):
        import os
        import tempfile

        from porsche_guru.cli import load_dotenv

        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("# comment\nexport PG_TEST_A='from-file'\nPG_TEST_B=from-file\n")
        os.environ["PG_TEST_B"] = "from-shell"
        try:
            load_dotenv(f.name)
            self.assertEqual(os.environ["PG_TEST_A"], "from-file")
            self.assertEqual(os.environ["PG_TEST_B"], "from-shell")
        finally:
            os.unlink(f.name)
            os.environ.pop("PG_TEST_A", None)
            os.environ.pop("PG_TEST_B", None)


if __name__ == "__main__":
    unittest.main()
