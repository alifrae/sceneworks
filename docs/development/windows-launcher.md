# Windows launcher

SceneWorks can be started from the repository root with one command:

```powershell
.\start-sceneworks.ps1
```

or by double-clicking `start-sceneworks.cmd`.

The launcher:

- verifies `uv` and `npm` are available;
- installs frontend dependencies when `node_modules` is missing;
- builds the Next.js production frontend when the existing build is missing or older than the frontend source;
- starts the FastAPI backend in its own PowerShell window;
- waits for `/api/health`;
- starts the frontend in its own PowerShell window;
- waits for port 3000 and opens SceneWorks in the default browser.

Normal use should stay in production mode because Next.js development mode can compile a route the first time it is visited, which makes menu navigation look slower than the API actually is.

For UI development:

```powershell
.\start-sceneworks.ps1 -Dev
```

Force a fresh frontend production build:

```powershell
.\start-sceneworks.ps1 -Rebuild
```

Start without opening the browser:

```powershell
.\start-sceneworks.ps1 -NoBrowser
```

The launcher does not stop existing SceneWorks processes. If the API or frontend health URL is already reachable, that service is reused rather than duplicated.
