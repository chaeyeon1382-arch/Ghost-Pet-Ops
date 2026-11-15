# Ghost Pet Ops

A retro virtual ghost pet whose mood follows the health of a web service. Built for Kiroween 2026 (Frankenstein category).

Statuses: `unknown`, `healthy`, `slow`, `down`, `recovering`. The app can read its data from mock data with demo buttons (the default) or from the real `GET /status` API.

```bash
npm install
npm run dev      # local dev server (mock data)
npm test         # run unit tests once
npm run build    # typecheck + production build
```

Node.js **22.12 or newer** is needed (Vitest 5 requires it; Vite 8 alone would accept 20.19). Check with `node -v`.

- Decision logic: `src/domain/petState.ts`
- Specs: `.kiro/specs/mvp/` (frontend), `.kiro/specs/state-store-read-api/` (backend), `.kiro/specs/frontend-status-api/` (connecting the frontend to `/status`)
- Backend (DynamoDB, `GET /status`, SAM): [`backend/README.md`](backend/README.md)
- Team rules: `.kiro/steering/`

## Data source: mock or real API

The source is chosen **at build time** with `VITE_*` variables. They hold no secrets. Vite copies them into the bundle.

| Variable | Values | Default | Meaning |
|---|---|---|---|
| `VITE_DATA_SOURCE` | `mock`, `api` | `mock` | `mock`: built-in demo data and demo buttons, no network. `api`: the app requests the relative path `/status`. Any other value means `mock`. |
| `VITE_POLL_INTERVAL_MS` | number | `10000` | Time between requests in `api` mode. Limited to 5000–10000. A value that is not a number gives the default. |

```bash
VITE_DATA_SOURCE=api npm run build        # production build that reads /status
```

In `api` mode:
- The app requests `/status` on load, then again every 10 seconds (see above). It stops while the browser tab is hidden and requests again as soon as the tab is visible.
- One failed request keeps the data fully visible. After **two failed requests in a row**, the last good data is dimmed and a "NO SIGNAL" overlay shows "stale since HH:MM" (your local time, for example `14:05 GMT+9`). The next good response clears it by itself.
- After an HTTP 429 the app waits longer (it uses `Retry-After` if present, otherwise it doubles the wait, up to 60 seconds).
- The pet shows the **worst** status among the services (down, slow, recovering, unknown, healthy). Every service also has its own row.
- The demo buttons are not shown. A way to inject failures into the real API is planned for a later spec.
- The checker that writes real health checks does not exist yet. Until it does, the real API returns every service as `unknown`, so the pet waits ("Waiting for the first health check..."). To see other moods, use mock mode or `DEV_FAKE_STATUS` below.

The page must be served from the same origin as `/status` (for example behind one CloudFront distribution, see `.kiro/specs/state-store-read-api/design.md` section 6.3).

## Trying `api` mode in development (no backend needed)

Two **dev-only** variables decide what answers `/status` while `npm run dev` runs. Set **at most one**. They are read by `vite.config.ts`, are not `VITE_`-prefixed (so the browser code never sees them), and are ignored by `npm run build`.

| Variable | Effect |
|---|---|
| `DEV_FAKE_STATUS=<scenario>` | `/status` returns the mock data for one scenario: `unknown`, `healthy`, `slow`, `down` or `recovering`. |
| `DEV_API_TARGET=<url>` | `/status` is forwarded to that `http://` or `https://` URL by the dev server's proxy. |

Variables are read when the server starts. To change one, stop the server (`Ctrl+C`) and start it again.

```bash
# mock mode (default): demo buttons, no /status request
npm run dev

# api mode with fake data, one command per scenario
VITE_DATA_SOURCE=api DEV_FAKE_STATUS=unknown npm run dev
VITE_DATA_SOURCE=api DEV_FAKE_STATUS=healthy npm run dev
VITE_DATA_SOURCE=api DEV_FAKE_STATUS=slow npm run dev
VITE_DATA_SOURCE=api DEV_FAKE_STATUS=down npm run dev
VITE_DATA_SOURCE=api DEV_FAKE_STATUS=recovering npm run dev

# api mode with a /status that cannot be reached (nothing listens on port 9)
VITE_DATA_SOURCE=api DEV_API_TARGET=http://127.0.0.1:9 npm run dev
```

With the closed port you should see "Loading…" for about 10 seconds. The first request fails at page load and the second one 10 seconds later. Then the "NO SIGNAL" overlay appears with the caption "Can't reach the ghost. Retrying…". There is no "stale since" line, because the app never received good data.

**Optional: see the overlay over real data, and the recovery.** This needs two terminals. In the first terminal, start a tiny local server that serves one valid response (Python is normally preinstalled in Codespaces):

```bash
mkdir -p /tmp/fake-api
echo '{"services":[{"id":"site-1","name":"Example","status":"healthy","responseMs":84,"lastChecked":"2026-11-15T02:00:00Z"}],"events":[]}' > /tmp/fake-api/status
python3 -m http.server 8000 --bind 127.0.0.1 --directory /tmp/fake-api
```

In the second terminal, run the app against it:

```bash
VITE_DATA_SOURCE=api DEV_API_TARGET=http://127.0.0.1:8000 npm run dev
```

Open the page. The pet and the service appear. Now stop the first terminal's server with `Ctrl+C`. After two failed requests (10 to 20 seconds) the data dims and the overlay shows "stale since HH:MM". Start the Python server again (up-arrow, then Enter). Within one polling interval the overlay disappears.

Simulating an HTTP 429 is not possible this way. Unit tests cover it.

## Seeing the running app in GitHub Codespaces

A codespace is a computer in GitHub's cloud with an editor in your browser. You do not install anything on your own machine. You need a GitHub account that can use Codespaces.

### 1. Open a codespace on the branch
1. Sign in to GitHub and open the repository page: `https://github.com/chaeyeon1382-arch/Ghost-Pet-Ops`.
2. Above the file list, click the branch button (it says `main`). Choose `feature/frontend-status-api`. (After the pull request is merged, stay on `main`.)
3. Click the green **Code** button, open the **Codespaces** tab, and click **Create codespace on feature/frontend-status-api**.
4. Wait about a minute. A Visual Studio Code editor opens in the browser tab.

### 2. Open a terminal
In the editor, open the menu (the three-line icon at the top left), choose **Terminal**, then **New Terminal**. A panel opens at the bottom with a command prompt. All commands below are typed there.

### 3. Check the Node version
```bash
node -v
```
It must print `v22.12.0` or higher, or `v24` or higher. Older versions (for example `v20.x`) cannot run the tests and may fail to start Vite.

If it is too old, install a newer one with nvm (normally available in Codespaces):
```bash
nvm install 22
nvm use 22
nvm alias default 22
node -v
```
The last command must now print `v22.something` (22.12 or higher). The `alias` line makes new terminals use it too. If `nvm` is not found, tell the maintainers which image your codespace uses.

### 4. Install and start
```bash
npm ci
```
This takes about half a minute. Then start one of the modes from the section above, for example mock mode:
```bash
npm run dev
```
The terminal prints a line with `Local: http://localhost:5173/`. The codespace shows a pop-up "Your application running on port 5173 is available". Click **Open in Browser**.
If you missed the pop-up: open the **Ports** tab in the bottom panel, find port `5173`, and click the globe icon (**Open in Browser**) in that row.

A forwarded port is private to your GitHub account by default. Do not make it public.

To use another mode, press `Ctrl+C` in the terminal to stop the server, then run another command from the previous section.

### 5. Stop and delete the codespace afterwards
- **Stop the app:** `Ctrl+C` in the terminal.
- **Stop the codespace:** close the browser tab (it stops by itself after 30 minutes without activity), or click the **Codespaces** label at the bottom left of the editor and choose **Stop Current Codespace**. A stopped codespace keeps its files and uses no compute hours (it still uses a little storage).
- **Delete the codespace:** open `https://github.com/codespaces`, click the **…** menu next to the codespace and choose **Delete**. Do this when you no longer need it. GitHub also deletes a codespace by itself after it has been stopped for 30 days (the default). Deleting loses anything not pushed to GitHub, and you do not need to push anything from the codespace.
- Running codespaces use your plan's free monthly hours. Check your allowance at `https://github.com/settings/billing`.

### If something goes wrong
- **`npm ci` or `npm test` complains about the Node version:** repeat step 3.
- **The page says "Blocked request. This host ... is not allowed":** the dev server rejected the codespace's address. `vite.config.ts` allows the codespace domain automatically, so this should not happen. Tell the maintainers the full message.
- **`api` mode without `DEV_FAKE_STATUS` or `DEV_API_TARGET`:** nothing answers `/status`, so the "NO SIGNAL" overlay appears after about 10 seconds. That is expected. Start the app with one of the commands above.

Licensed under the MIT License. See [LICENSE](LICENSE).
