import pytest
from pydantic import ValidationError

from app.agents.graph import build_graph
from app.agents.tools import PermittedTestsInput


def test_langgraph_contains_bounded_investigation_stages():
    graph = build_graph().get_graph()
    nodes = set(graph.nodes)
    assert {"triage", "investigation", "code_analysis", "knowledge", "aggregate", "resolution", "verification", "request_approval", "finalize"} <= nodes
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("triage", "investigation") in edges
    assert ("triage", "code_analysis") in edges
    assert ("triage", "knowledge") in edges


def test_permitted_test_tool_rejects_arbitrary_commands():
    with pytest.raises(ValidationError):
        PermittedTestsInput(test_name="powershell -Command Remove-Item data")
