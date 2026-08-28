# WP11 validation scope

WP11 is merge-ready only after automated qualification covers:

- backend unit/integration tests;
- MCP Observe/Standard/Advanced tool-surface and server-side gates;
- persistent Gemini ACP `session/new` + `session/load` behavior using the mock ACP agent;
- Advanced-session worktree isolation and permission ceilings;
- adaptive workflow routing regression tests;
- migration upgrade through revision `0008`;
- frontend type/build checks for Settings -> ChatGPT / MCP.

A real ChatGPT Secure MCP Tunnel/plugin connection remains an operator acceptance test because CI does not have access to the user's ChatGPT account or local SceneWorks instance. The setup procedure is documented in `docs/tutorials/chatgpt-mcp-plugin.md`.
