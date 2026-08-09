"""Mock ACP v1 server (agent side) for testing GeminiACPBackend without Gemini.

Speaks the same protocol subset as Gemini CLI 0.53.x:
- agent methods: initialize, session/new, session/prompt, session/cancel, session/close
- client methods used: fs/write_text_file (with a response wait), session/request_permission
- notifications: session/update (agent_message_chunk, tool_call, agent_thought_chunk)

Behavior is driven by environment variables:
- MOCK_ACP_WRITE_MODE=1: issue an fs/write_text_file client request for file "mock.txt"
- MOCK_ACP_DENY_MODE=1: issue a session/request_permission client request with only
  reject options available
- MOCK_ACP_FAILDELAY=n: emit a failing agent_error-like event (used for timeout tests)
- MOCK_ACP_STOP=refusal|max_tokens|end_turn: stopReason returned
"""

from __future__ import annotations

import json
import os
import sys


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def send_request(method: str, params: dict, request_id: int) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})


def main() -> int:
    if "--version" in sys.argv:
        print("0.53.1 (mock)")
        return 0
    write_mode = os.environ.get("MOCK_ACP_WRITE_MODE") == "1"
    stop = os.environ.get("MOCK_ACP_STOP", "end_turn")
    request_id = 9000
    session_id = "mock-session-1"
    pending_client_requests: dict[int, str] = {}
    prompt_request_id = None
    sent_chunks = False

    def send_completion() -> None:
        """Send the remaining chunks and the prompt response (end of turn)."""
        if not sent_chunks:
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "tool_call",
                            "title": "read_file",
                            "kind": "read",
                            "status": "in_progress",
                        },
                    },
                }
            )
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "title": "read_file",
                            "kind": "read",
                            "status": "completed",
                        },
                    },
                }
            )
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "I inspected the code."},
                    },
                },
            }
        )
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {"type": "text", "text": "Considering the fix."},
                    },
                },
            }
        )
        send(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": session_id,
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "VERDICT: APPROVED\nDone."},
                    },
                },
            }
        )
        send(
            {
                "jsonrpc": "2.0",
                "id": prompt_request_id,
                "result": {"stopReason": stop, "usage": {"inputTokens": 10, "outputTokens": 5}},
            }
        )

    for raw in sys.stdin:
        if not raw.strip():
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        message_id = message.get("id")
        method = message.get("method", "")
        params = message.get("params") or {}

        if message_id in pending_client_requests:
            # Response to one of our client requests: finish the turn.
            pending = pending_client_requests.pop(message_id)
            if pending == "write":
                if "error" in message:
                    send(
                        {
                            "jsonrpc": "2.0",
                            "method": "session/update",
                            "params": {
                                "sessionId": session_id,
                                "update": {
                                    "sessionUpdate": "agent_thought_chunk",
                                    "content": {"type": "text", "text": "Write was denied by the client."},
                                },
                            },
                        }
                    )
            send_completion()
            continue

        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {
                        "protocolVersion": 1,
                        "agentInfo": {"name": "gemini-cli", "title": "Gemini CLI (mock)", "version": "0.53.1"},
                        "agentCapabilities": {"loadSession": True, "promptCapabilities": {"text": True}},
                    },
                }
            )
        elif method == "session/new":
            send({"jsonrpc": "2.0", "id": message_id, "result": {"sessionId": session_id}})
        elif method == "session/prompt":
            prompt_request_id = message_id
            if stop == "refusal":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": message_id,
                        "result": {"stopReason": "refusal", "usage": {"inputTokens": 1, "outputTokens": 1}},
                    }
                )
                continue
            if write_mode:
                request_id += 1
                pending_client_requests[request_id] = "write"
                send_request(
                    "fs/write_text_file",
                    {
                        "sessionId": session_id,
                        "path": "mock.txt",
                        "content": "created by mock agent\n",
                    },
                    request_id,
                )
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "tool_call",
                                "title": "edit",
                                "kind": "edit",
                                "status": "in_progress",
                            },
                        },
                    }
                )
            else:
                send_completion()
        elif method == "session/cancel":
            send({"jsonrpc": "2.0", "id": message_id, "result": {}})
        elif method == "session/close":
            send({"jsonrpc": "2.0", "id": message_id, "result": {}})
            return 0
        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {"code": -32601, "message": f'"Method not found": {method}'},
                }
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
