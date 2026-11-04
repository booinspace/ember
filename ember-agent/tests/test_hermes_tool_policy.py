"""Model-visible tools reflect current host grants and exact broker-issued references."""

from jsonschema import Draft202012Validator

from ember_agent.inference.contracts import ToolDefinition, ToolFunction
from ember_agent.runtime.hermes_tasks import available_model_tools

ADVERTISED = tuple(
    ToolDefinition(function=ToolFunction(name=name, parameters={"type": "object"}))
    for name in ("searx_search", "ember_recall", "ember_finish", "shell")
)


def test_initial_authority_excludes_ungranted_recall_unknown_tools_and_premature_finish() -> None:
    tools = available_model_tools(
        ADVERTISED,
        search_remaining=3,
        recall_available=False,
        evidence_available=False,
        citation_ids=(),
    )
    assert [tool.function.name for tool in tools] == ["searx_search"]
    assert "3 search call(s) remain" in tools[0].function.description


def test_exhausted_search_and_consumed_private_recall_leave_only_grounded_finish() -> None:
    for references in (("q1-s2", "q3-s1"), ("memory",)):
        tools = available_model_tools(
            ADVERTISED,
            search_remaining=0,
            recall_available=False,
            evidence_available=True,
            citation_ids=references,
        )
        assert [tool.function.name for tool in tools] == ["ember_finish"]
        validator = Draft202012Validator(tools[0].function.parameters)
        assert validator.is_valid({"summary": "Supported finding", "citations": [references[0]]})
        assert not validator.is_valid({"summary": "Unsupported", "citations": ["q1-s1"]})
        assert not validator.is_valid(
            {"summary": "Unsupported", "citations": ["https://public.example/"]}
        )
        assert not validator.is_valid({"summary": "Missing source", "citations": []})


def test_empty_search_evidence_allows_only_an_empty_citation_gap_report() -> None:
    tools = available_model_tools(
        ADVERTISED,
        search_remaining=0,
        recall_available=False,
        evidence_available=True,
        citation_ids=(),
    )
    validator = Draft202012Validator(tools[0].function.parameters)
    assert validator.is_valid({"summary": "No useful source found.", "citations": []})
    assert not validator.is_valid({"summary": "Invented", "citations": ["q1-s1"]})


def test_host_policy_does_not_add_tools_absent_from_the_original_request() -> None:
    assert (
        available_model_tools(
            (),
            search_remaining=3,
            recall_available=True,
            evidence_available=True,
            citation_ids=("memory",),
        )
        == ()
    )
