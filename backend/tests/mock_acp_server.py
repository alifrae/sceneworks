"""Mock ACP v1 server for GeminiACPBackend/Advanced-session tests.

Speaks the protocol subset SceneWorks uses:
- initialize, session/new, session/load, session/prompt, session/cancel, session/close
- client fs/terminal/permission requests used by policy tests
- session/update streaming notifications

Behavior is driven by environment variables:
- MOCK_ACP_WRITE_MODE=1: request fs/write_text_file for ``mock.txt``
- MOCK_ACP_READ_MODE=1: request fs/read_text_file
- MOCK_ACP_TERMINAL_MODE=1: request terminal/create
- MOCK_ACP_EXECUTE_MODE=1: request execute permission
- MOCK_ACP_UNKNOWN_METHOD=1: issue an unknown client method
- MOCK_ACP_DENY_MODE=1: request permission with only reject options
- MOCK_ACP_HOLD_PROMPT=1: keep prompt pending until cancellation
- MOCK_ACP_NO_LOAD_SESSION=1: initialize advertises loadSession=false
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


def _session_metadata() -> dict:
    return {
        "modes": {
            "availableModes": [
                {"id": "default", "name": "Default"},
                {"id": "plan", "name": "Plan"},
            ],
            "currentModeId": "default",
        },
        "models": {
            "availableModels": [{"modelId": "mock-model", "name": "Mock model"}],
            "currentModelId": "mock-model",
        },
    }


def main() -> int:
    if "--version" in sys.argv:
        print("0.53.1 (mock)")
        return 0

    write_mode = os.environ.get("MOCK_ACP_WRITE_MODE") == "1"
    read_mode = os.environ.get("MOCK_ACP_READ_MODE") == "1"
    terminal_mode = os.environ.get("MOCK_ACP_TERMINAL_MODE") == "1"
    execute_mode = os.environ.get("MOCK_ACP_EXECUTE_MODE") == "1"
    unknown_mode = os.environ.get("MOCK_ACP_UNKNOWN_METHOD") == "1"
    deny_mode = os.environ.get("MOCK_ACP_DENY_MODE") == "1"
    hold_prompt = os.environ.get("MOCK_ACP_HOLD_PROMPT") == "1"
    load_session = os.environ.get("MOCK_ACP_NO_LOAD_SESSION") != "1"
    stop = os.environ.get("MOCK_ACP_STOP", "end_turn")

    request_id = 9000
    session_id = "mock-session-1"
    pending_client_requests: dict[int, str] = {}
    prompt_request_id = None
    sent_chunks = False

    def send_completion() -> None:
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
                "result": {
                    "stopReason": stop,
                    "usage": {"inputTokens": 10, "outputTokens": 5},
                },
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
            pending = pending_client_requests.pop(message_id)
            if pending in ("write", "read", "terminal", "permission", "unknown"):
                if "error" in message:
                    send(
                        {
                            "jsonrpc": "2.0",
                            "method": "session/update",
                            "params": {
                                "sessionId": session_id,
                                "update": {
                                    "sessionUpdate": "agent_thought_chunk",
                                    "content": {
                                        "type": "text",
                                        "text": f"Client denied {pending} request.",
                                    },
                                },
                            },
                        }
                    )
            title = "run_command" if pending == "terminal" else "mock_tool"
            kind = "execute" if pending == "terminal" else pending
            send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": session_id,
                        "update": {
                            "sessionUpdate": "tool_call",
                            "title": title,
                            "kind": kind,
                            "status": "in_progress",
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
                        "agentInfo": {
                            "name": "gemini-cli",
                            "title": "Gemini CLI (mock)",
                            "version": "0.53.1",
                        },
                        "agentCapabilities": {
                            "loadSession": load_session,
                            "promptCapabilities": {"text": True},
                            "mcpCapabilities": {"http": True, "sse": True},
                        },
                    },
                }
            )
        elif method == "session/new":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {"sessionId": session_id, **_session_metadata()},
                }
            )
        elif method == "session/load":
            if not load_session:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": message_id,
                        "error": {"code": -32601, "message": "loadSession unavailable"},
                    }
                )
            else:
                # A fresh mock process deliberately accepts the stable session id.
                # This models Gemini CLI restoring provider-owned conversation state.
                session_id = str(params.get("sessionId") or session_id)
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": message_id,
                        "result": _session_metadata(),
                    }
                )
        elif method == "session/prompt":
            prompt_request_id = message_id
            if stop == "refusal":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": message_id,
                        "result": {
                            "stopReason": "refusal",
                            "usage": {"inputTokens": 1, "outputTokens": 1},
                        },
                    }
                )
                continue
            if hold_prompt:
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "agent_thought_chunk",
                                "content": {
                                    "type": "text",
                                    "text": "MOCK_PROMPT_HELD",
                                },
                            },
                        },
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
            elif read_mode:
                request_id += 1
                pending_client_requests[request_id] = "read"
                send_request(
                    "fs/read_text_file",
                    {"sessionId": session_id, "path": "README.md"},
                    request_id,
                )
            elif terminal_mode:
                request_id += 1
                pending_client_requests[request_id] = "terminal"
                send_request(
                    "terminal/create",
                    {
                        "sessionId": session_id,
                        "command": "echo",
                        "args": ["hello"],
                    },
                    request_id,
                )
            elif execute_mode:
                request_id += 1
                pending_client_requests[request_id] = "permission"
                send_request(
                    "session/request_permission",
                    {
                        "sessionId": session_id,
                        "toolCall": {"kind": "execute", "title": "run tests"},
                        "options": [
                            {"optionId": "allow-once", "name": "allow_once"},
                            {"optionId": "deny-once", "name": "reject_once"},
                        ],
                    },
                    request_id,
                )
            elif deny_mode:
                request_id += 1
                pending_client_requests[request_id] = "permission"
                send_request(
                    "session/request_permission",
                    {
                        "sessionId": session_id,
                        "toolCall": {
                            "kind": "execute",
                            "title": "dangerous command",
                        },
                        "options": [{"optionId": "reject-1", "name": "reject"}],
                    },
                    request_id,
                )
            elif unknown_mode:
                request_id += 1
                pending_client_requests[request_id] = "unknown"
                send_request("unsupported/capability", {"param": 1}, request_id)
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
                    "error": {
                        "code": -32601,
                        "message": f'"Method not found": {method}',
                    },
                }
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
