# AgentBench Lab — web UI

React + TypeScript + Vite. Reads everything from the backend API at runtime;
there is no fixture, mock or hardcoded metric anywhere in `src/`.

```bash
npm install
npm run dev        # http://127.0.0.1:5173
npm run build      # tsc -b && vite build
npx tsc --noEmit   # typecheck only
```

Point it at an API with `VITE_API_BASE_URL` (see `.env.example`); it defaults to
`http://127.0.0.1:8000/api/v1`.

## Dependencies

`react-router-dom` (routing), `@tanstack/react-query` (fetching and polling),
`recharts` (charts), `tailwindcss` (styling). No component library, no CSS-in-JS
runtime, no state manager — primitives live in `src/components/ui.tsx`.

## Why `src/components/domain.tsx` exists

The honesty rules this product is built on are enforced by components rather
than left to convention:

- `RateDisplay` cannot render a bare percentage. It takes the API's `Rate`
  object and always prints the numerator, denominator, confidence interval and
  small-sample warning.
- `OutcomeBadge` colours the four outcomes that count toward a success rate
  differently from the four that are excluded, and states which it is.
- `SimulatedBadge` marks any result produced by a non-model agent.
- `OptionalNumber` renders "not reported" for `null`, so an unreported token
  count never displays as zero.

## Layout

```
src/
  api/        client.ts (fetch + typed errors), types.ts, hooks.ts
  components/ ui.tsx (primitives), domain.tsx (honesty components), Layout.tsx
  screens/    Overview, Benchmarks, Agents, Experiments, ExperimentDetail,
              RunDetail, Comparisons, Failures, Settings
  router.tsx  main.tsx  index.css
screenshots/  captured from a real browser run
```

`RunDetail` consumes `GET /runs/{id}/events/stream` over `EventSource` while a
run is still executing, and merges the live tail with the stored event page.
