# Screenshot plan

No images exist yet. Add them only from a deployment that the owner approved (`.kiro/specs/aws-deploy-demo/tasks.md`, F-3).

**Naming:** `<number>-<state>-<viewport>.png`, for example `01-unknown-desktop.png`.

| # | State | Viewport | File |
|---|---|---|---|
| 1 | `unknown` (before the first check) | desktop 1280 px | `01-unknown-desktop.png` |
| 2 | `healthy` | desktop | `02-healthy-desktop.png` |
| 3 | `healthy` | mobile 360 px | `03-healthy-mobile.png` |
| 4 | `slow` | desktop | `04-slow-desktop.png` |
| 5 | `down` | desktop | `05-down-desktop.png` |
| 6 | `down` | mobile | `06-down-mobile.png` |
| 7 | `recovering` | desktop | `07-recovering-desktop.png` |
| 8 | events list after a full demo run | desktop | `08-events-desktop.png` |
| 9 | NO SIGNAL overlay | desktop | `09-no-signal-desktop.png` |

Shot 9 comes from the local method (`VITE_DATA_SOURCE=api DEV_API_TARGET=http://127.0.0.1:9 npm run dev`), because a deployed stack cannot be made unreachable without a change.

**Before every commit, check each image for:**
- no browser address bar (it shows the CloudFront domain),
- no AWS console area,
- no AWS account id, email address, bucket name or distribution id,
- no personal data from the browser (bookmarks, profile name).

Crop or retake the image if any of these is visible. Commit images in their own pull request.
