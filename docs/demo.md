# Demo guide

Nothing described here is deployed yet. Every AWS step needs the owner's approval, because it can cost money.
Spec: `.kiro/specs/aws-deploy-demo/`. Commands use placeholders (`<bucket>`, `<distribution-id>`, `<region>`). Never write real values in a committed file.

## 1. Before the demo

1. The checker author's pull request is merged, and the pre-deploy checks pass (`backend/README.md`).
2. Stacks are deployed in the order given in `backend/README.md`. The schedule is **on** (it is created off).
3. **Call the demo URL directly and check for HTTP 200.** A public Function URL needs two permissions in its resource policy (`lambda:InvokeFunctionUrl` and `lambda:InvokeFunction`). If one is missing, every call returns 403, and the checker counts the demo target as `down` in every mode ([AWS docs](https://docs.aws.amazon.com/gb_gb/lambda/latest/dg/urls-auth.html)).
   ```bash
   curl -sS -o /dev/null -w "%{http_code}\n" "<demo-url>"     # expect 200
   ```
   Also check that the response text is `demo target: ok`.
4. Open the CloudFront page. After 2 to 3 minutes every service row must show a status and a check time, not `unknown` and `—`. If they stay `unknown`, the schedule is probably still off.
5. Check that the two real sites are `healthy`. The pet shows the worst status of all services, so one site that is not `healthy` hides what the demo target does.

## 2. Demo script

Change `MODE` in the Lambda console: open the demo function, then **Configuration**, **Environment variables**, **Edit**, change `MODE`, **Save**. The next call uses the new value. Console edits make the stack differ from its template, which is fine for a stack that is deleted after the demo.

The checker runs every 60 s, so the first check after a change comes after 0 to 60 s. The page adds up to 15 s (10 s polling plus 5 s edge cache). Times below are from `decision.py`, and have not been observed yet.

| Step | Set `MODE` | Expected demo row | Wait |
|---|---|---|---|
| 1 | `ok` | `HEALTHY` | start state |
| 2 | `slow` | `SLOW` (2 slow checks, each over 2000 ms) | 1 to 2 min |
| 3 | `down` | `DOWN` (3 failed checks) | 2 to 3 min |
| 4 | `ok` | `RECOVERING` after the first success | up to 1 min |
| 5 | (stay `ok`) | `HEALTHY` after one more fast check | 1 to 2 min after step 4 starts |

A full run takes about 8 minutes. The event list shows each change. During step 3, the pet changes to its `down` look. After step 5, it returns to `healthy`.

Write down the real times in `design.md` section 7.2 after the first run.

## 3. Layout check list (desktop and mobile)

Check each item at 360 px, 768 px and 1280 px wide. On a phone, check portrait and landscape.

- [ ] The five pet states look right: `unknown`, `healthy`, `slow`, `down`, `recovering`.
- [ ] The NO SIGNAL overlay: data dims, "stale since HH:MM" shows, the data stays readable.
- [ ] Service rows: long names wrap, nothing overflows, no horizontal scroll.
- [ ] Event list: rows wrap, newest first, a removed service shows its raw id.
- [ ] Tap targets are large enough (mock mode only has demo buttons).
- [ ] Text contrast is enough, also in the dimmed state, and meaning does not depend on color alone.
- [ ] Loading state ("Loading…") shows before the first response.
- [ ] Reload works, and the page keeps working in a background tab that becomes visible again.

Record failures as issues for the frontend owner. The deployment work does not change `src/`.

## 4. Screenshots

See [`docs/screenshots/README.md`](screenshots/README.md). Images are added only from a deployment that the owner approved.

## 5. After the demo

Delete the demo stack and remove its entry from `services.json`, empty the bucket and delete the frontend stack, delete the backend stack, and keep or delete the budget stack. Look at Cost Explorer the next day. Details: `.kiro/specs/aws-deploy-demo/design.md` section 9.4.
