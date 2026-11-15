# Ghost Pet Ops

A retro virtual ghost pet whose mood follows the health of a web service. Built for Kiroween 2026 (Frankenstein category).

Statuses: `unknown`, `healthy`, `slow`, `down`, `recovering`. The current build uses mock data with demo buttons.

```bash
npm install
npm run dev      # local dev server
npm test         # run unit tests once
npm run build    # typecheck + production build
```

- Decision logic: `src/domain/petState.ts`
- Spec: `.kiro/specs/mvp/`
- Team rules: `.kiro/steering/`

Licensed under the MIT License. See [LICENSE](LICENSE).
