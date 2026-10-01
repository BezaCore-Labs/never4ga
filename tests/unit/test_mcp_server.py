"""The MCP server loop: handshake, listing, calling, and how failure travels.

Specification: details/api-cli-mcp-contract.md sections 5 and 12.

Never4gA implements this protocol itself rather than through an SDK, so the
handshake is tested rather than trusted.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from typing import Any

import pytest

from never4ga.errors import StructuredError, WorkItemNotFoundError
from never4ga.mcp.jsonrpc import INVALID_PARAMS, METHOD_NOT_FOUND, Request
from never4ga.mcp.server import MCP_PROTOCOL_VERSION, McpServer, ToolFailureError
from never4ga.mcp.tools import Tool, schema


def echo(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"echoed": dict(arguments)}


def refuse(_: Mapping[str, Any]) -> Mapping[str, Any]:
    raise ToolFailureError(
        StructuredError(
            "index_not_built",
            "this vault has not been indexed yet",
            {"vault": "/somewhere"},
            repair_hint="run `never4ga index`",
        )
    )


def explode(_: Mapping[str, Any]) -> Mapping[str, Any]:
    raise RuntimeError("something nobody anticipated")


def raises_domain(_: Mapping[str, Any]) -> Mapping[str, Any]:
    raise WorkItemNotFoundError("openproject has no work item '1'")


def server() -> McpServer:
    return McpServer(
        [
            Tool("never4ga_echo", "Echo the arguments.", echo, schema({"a": {"type": "string"}})),
            Tool("never4ga_refuse", "Always refuses.", refuse),
            Tool("never4ga_explode", "Always raises.", explode),
            Tool("never4ga_domain", "Raises a domain error.", raises_domain),
        ]
    )


def call(method: str, identifier: Any = 1, **params: Any) -> Request:
    return Request(method=method, id=identifier, params=params)


class TestHandshake:
    def test_initialize_states_the_protocol_and_the_server(self) -> None:
        result = server().handle(call("initialize"))
        assert result is not None
        assert result["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION
        assert result["result"]["serverInfo"]["name"] == "never4ga"

    def test_only_tools_are_advertised(self) -> None:
        # Resources would restate what a tool already answers, and advertising
        # a capability nothing implements is a false claim.
        result = server().handle(call("initialize"))
        assert result is not None
        assert set(result["result"]["capabilities"]) == {"tools"}

    def test_the_initialized_notification_is_not_answered(self) -> None:
        # The protocol rule the whole notification distinction exists for.
        message = Request(method="notifications/initialized", is_notification=True)
        assert server().handle(message) is None

    def test_ping_answers_empty(self) -> None:
        result = server().handle(call("ping"))
        assert result is not None
        assert result["result"] == {}

    def test_an_unknown_method_is_a_protocol_error(self) -> None:
        result = server().handle(call("tools/invent"))
        assert result is not None
        assert result["error"]["code"] == METHOD_NOT_FOUND

    def test_an_unknown_notification_is_still_not_answered(self) -> None:
        # A notification must not be answered *even when it fails*, or the
        # client receives something it is not reading for.
        message = Request(method="notifications/invented", is_notification=True)
        assert server().handle(message) is None


class TestListing:
    def test_every_tool_carries_a_name_a_description_and_a_schema(self) -> None:
        result = server().handle(call("tools/list"))
        assert result is not None
        for tool in result["result"]["tools"]:
            assert tool["name"].startswith("never4ga_")
            assert tool["description"]
            assert tool["inputSchema"]["type"] == "object"

    def test_the_schema_names_required_arguments(self) -> None:
        built = Tool("never4ga_x", "d", echo, schema({"q": {"type": "string"}}, required=("q",)))
        assert built.describe()["inputSchema"]["required"] == ["q"]


class TestCalling:
    def test_a_result_carries_the_payload_twice_and_identically(self) -> None:
        # The structured half is what a client parses and the text half is what
        # a model reads. They are the same object, so neither can drift from
        # the other.
        result = server().handle(call("tools/call", name="never4ga_echo", arguments={"a": "b"}))
        assert result is not None
        content = result["result"]
        assert content["structuredContent"] == {"echoed": {"a": "b"}}
        assert json.loads(content["content"][0]["text"]) == content["structuredContent"]
        assert content["isError"] is False

    def test_a_tool_that_does_not_exist_is_a_protocol_error(self) -> None:
        result = server().handle(call("tools/call", name="never4ga_nope", arguments={}))
        assert result is not None
        assert result["error"]["code"] == METHOD_NOT_FOUND

    def test_a_missing_tool_name_is_invalid_params(self) -> None:
        result = server().handle(call("tools/call", arguments={}))
        assert result is not None
        assert result["error"]["code"] == INVALID_PARAMS

    def test_arguments_that_are_not_an_object_are_invalid_params(self) -> None:
        result = server().handle(call("tools/call", name="never4ga_echo", arguments=[1]))
        assert result is not None
        assert result["error"]["code"] == INVALID_PARAMS


class TestFailure:
    def test_a_refusal_is_a_tool_error_the_model_can_read(self) -> None:
        # Not a protocol error: the message was well formed and the vault was
        # not ready, which is something a model should see and act on.
        result = server().handle(call("tools/call", name="never4ga_refuse", arguments={}))
        assert result is not None
        assert "error" not in result
        content = result["result"]
        assert content["isError"] is True
        assert content["structuredContent"]["code"] == "index_not_built"

    def test_the_repair_hint_survives_the_transport(self) -> None:
        # Section 12: an MCP client must not be the one surface that cannot
        # tell a caller how to fix something.
        result = server().handle(call("tools/call", name="never4ga_refuse", arguments={}))
        assert result is not None
        assert result["result"]["structuredContent"]["repair_hint"] == "run `never4ga index`"

    def test_a_domain_error_becomes_a_tool_error_rather_than_a_crash(self) -> None:
        result = server().handle(call("tools/call", name="never4ga_domain", arguments={}))
        assert result is not None
        assert result["result"]["isError"] is True
        assert result["result"]["structuredContent"]["code"] == "WorkItemNotFoundError"

    def test_an_unanticipated_exception_does_not_take_the_server_down(self) -> None:
        built = server()
        result = built.handle(call("tools/call", name="never4ga_explode", arguments={}))
        assert result is not None
        assert "error" in result
        # And the server still answers afterwards, which is the thing that
        # matters: one bad tool must not end the session.
        after = built.handle(call("ping", 2))
        assert after is not None
        assert after["result"] == {}


class TestTheLoop:
    def test_a_whole_session_reads_and_answers_in_order(self) -> None:
        source = io.StringIO(
            "\n".join(
                [
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
                    json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                ]
            )
            + "\n"
        )
        sink = io.StringIO()
        server().serve(source, sink)
        answers = [json.loads(line) for line in sink.getvalue().splitlines()]
        # Two answers for three messages: the notification is not one.
        assert [answer["id"] for answer in answers] == [1, 2]

    def test_a_malformed_message_is_answered_and_the_loop_continues(self) -> None:
        source = io.StringIO(
            "{not json\n" + json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}) + "\n"
        )
        sink = io.StringIO()
        server().serve(source, sink)
        answers = [json.loads(line) for line in sink.getvalue().splitlines()]
        assert answers[0]["id"] is None
        assert answers[0]["error"]["code"] == -32700
        assert answers[1]["id"] == 9

    def test_nothing_is_written_for_a_stream_that_only_notifies(self) -> None:
        source = io.StringIO(json.dumps({"jsonrpc": "2.0", "method": "notifications/x"}) + "\n")
        sink = io.StringIO()
        server().serve(source, sink)
        assert sink.getvalue() == ""


@pytest.mark.parametrize("bad", ["", "   ", "\n"])
def test_an_empty_stream_ends_cleanly(bad: str) -> None:
    sink = io.StringIO()
    server().serve(io.StringIO(bad), sink)
    assert sink.getvalue() == ""
