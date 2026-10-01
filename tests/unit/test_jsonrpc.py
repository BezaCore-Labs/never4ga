"""JSON-RPC 2.0 over a byte stream, which is what MCP's stdio transport is.

Never4gA implements this itself rather than taking on a package with many
dependencies. That makes protocol correctness this project's problem, so it is
tested against the wire.

Specification:
- JSON-RPC 2.0 -- a request carries `jsonrpc`, `method`, `id` and optional
  `params`; a notification is the same without `id` and MUST NOT be answered.
- details/api-cli-mcp-contract.md section 12 -- Never4gA's errors carry a code,
  a message, details and a repair hint, and none of that may be lost crossing a
  transport that has its own error shape.

The surface is small and closed, which is what makes it safe to own.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from never4ga.mcp.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    JsonRpcError,
    Request,
    read_messages,
    write_message,
)


def stream(*payloads: Any) -> io.StringIO:
    return io.StringIO("".join(json.dumps(one) + "\n" for one in payloads))


def request(method: str = "ping", identifier: Any = 1, **params: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": identifier}
    if params:
        body["params"] = params
    return body


class TestReading:
    def test_a_request_is_parsed(self) -> None:
        [message] = list(read_messages(stream(request("tools/list"))))
        assert isinstance(message, Request)
        assert message.method == "tools/list"
        assert message.id == 1
        assert message.params == {}

    def test_params_survive(self) -> None:
        [message] = list(read_messages(stream(request("tools/call", name="x"))))
        assert message.params == {"name": "x"}

    def test_a_notification_has_no_id(self) -> None:
        # The distinction the protocol turns on: a notification MUST NOT be
        # answered, and `id: null` is a *request* with a null id rather than a
        # notification, so absence is what has to be detected.
        [message] = list(read_messages(stream({"jsonrpc": "2.0", "method": "x"})))
        assert message.is_notification is True

    def test_a_null_id_is_a_request_not_a_notification(self) -> None:
        [message] = list(read_messages(stream({"jsonrpc": "2.0", "method": "x", "id": None})))
        assert message.is_notification is False

    def test_several_messages_come_back_in_order(self) -> None:
        messages = list(read_messages(stream(request("a", 1), request("b", 2), request("c", 3))))
        assert [one.method for one in messages] == ["a", "b", "c"]

    def test_blank_lines_are_skipped(self) -> None:
        source = io.StringIO("\n" + json.dumps(request("a")) + "\n\n")
        assert [one.method for one in read_messages(source)] == ["a"]

    def test_a_string_id_is_kept_as_a_string(self) -> None:
        # Clients use both. Coercing one to the other means answering with an
        # id the caller cannot match.
        [message] = list(read_messages(stream(request("a", "abc"))))
        assert message.id == "abc"

    def test_unparseable_json_raises_a_parse_error(self) -> None:
        with pytest.raises(JsonRpcError) as raised:
            list(read_messages(io.StringIO("{not json\n")))
        assert raised.value.code == PARSE_ERROR

    def test_something_that_is_not_an_object_is_an_invalid_request(self) -> None:
        with pytest.raises(JsonRpcError) as raised:
            list(read_messages(stream([1, 2, 3])))
        assert raised.value.code == INVALID_REQUEST

    def test_a_missing_method_is_an_invalid_request(self) -> None:
        with pytest.raises(JsonRpcError) as raised:
            list(read_messages(stream({"jsonrpc": "2.0", "id": 1})))
        assert raised.value.code == INVALID_REQUEST

    def test_the_wrong_protocol_version_is_an_invalid_request(self) -> None:
        with pytest.raises(JsonRpcError) as raised:
            list(read_messages(stream({"jsonrpc": "1.0", "method": "x", "id": 1})))
        assert raised.value.code == INVALID_REQUEST

    def test_params_that_are_not_an_object_are_invalid(self) -> None:
        # The protocol allows positional params; MCP does not use them, and
        # accepting them silently would mean guessing what a caller meant.
        with pytest.raises(JsonRpcError) as raised:
            list(read_messages(stream({"jsonrpc": "2.0", "method": "x", "id": 1, "params": []})))
        assert raised.value.code == INVALID_PARAMS


class TestWriting:
    def test_a_message_is_one_line_of_json(self) -> None:
        out = io.StringIO()
        write_message(out, {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
        text = out.getvalue()
        assert text.endswith("\n")
        assert text.count("\n") == 1
        assert json.loads(text)["result"] == {"ok": True}

    def test_a_newline_inside_a_value_does_not_become_a_frame_boundary(self) -> None:
        # The framing is newline-delimited, so a value containing one has to be
        # escaped rather than written through. Otherwise a client would see
        # half a message.
        out = io.StringIO()
        write_message(out, {"jsonrpc": "2.0", "id": 1, "result": {"text": "a\nb"}})
        assert out.getvalue().count("\n") == 1
        assert json.loads(out.getvalue())["result"]["text"] == "a\nb"

    def test_non_ascii_survives(self) -> None:
        out = io.StringIO()
        write_message(out, {"jsonrpc": "2.0", "id": 1, "result": {"text": "Never4gA — ✓"}})
        assert json.loads(out.getvalue())["result"]["text"] == "Never4gA — ✓"


class TestErrors:
    def test_an_error_renders_to_the_protocol_shape(self) -> None:
        rendered = JsonRpcError(METHOD_NOT_FOUND, "no such method").as_response(7)
        assert rendered == {
            "jsonrpc": "2.0",
            "id": 7,
            "error": {"code": METHOD_NOT_FOUND, "message": "no such method"},
        }

    def test_structured_detail_travels_in_data(self) -> None:
        # details/api-cli-mcp-contract.md section 12's shape has nowhere to go
        # in JSON-RPC except `data`, and losing it would make an MCP client the
        # one surface that cannot tell a caller how to repair a failure.
        detail = {"code": "index_not_built", "repair_hint": "run `never4ga index`"}
        rendered = JsonRpcError(INTERNAL_ERROR, "not indexed", data=detail).as_response(1)
        assert rendered["error"]["data"] == detail

    def test_the_codes_are_the_protocol_s_own(self) -> None:
        assert (PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS) == (
            -32700,
            -32600,
            -32601,
            -32602,
        )
