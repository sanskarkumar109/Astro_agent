# Evaluation

The evaluation suite lives in `eval/` and is designed to make regressions visible instead of relying on a single demo conversation.

## Golden Set

`eval/golden_set.jsonl` contains 20 representative cases:

- Valid chart readings.
- Daily transit requests.
- Career and relationship readings.
- Missing birth details.
- Invalid dates, times, and places.
- Off-topic questions.
- Prompt-injection attempts.
- Medical, legal, and financial guardrails.

Each case declares expected intent, expected tools, required response phrases, and whether an error or safety behavior is expected.

## Deterministic Checks

The runner directly asserts:

- Intent classification.
- Required tool calls.
- Required graceful error behavior.
- Required safety language.
- Geocoding sanity for Delhi.
- Birth chart sanity using Swiss Ephemeris, including Sun longitude range and 12 houses.

If `pyswisseph` is missing, the chart sanity check fails explicitly. This is intentional: invented planetary positions would be worse than a visible setup failure.

## LLM-as-Judge

This version does not use an LLM judge. The response layer is deterministic enough that the first eval pass can rely on exact assertions. If an LLM response composer is added, I would add separate one-dimension judge rubrics for warmth, specificity, and groundedness, then manually spot-check at least 10 verdicts and report agreement.

## Metrics

Every run logs:

- Quality score.
- Failure rate.
- p50 and p95 latency.
- Tool-call count.
- Token estimate.
- Estimated LLM cost.

Current estimated LLM cost is `$0.00` in the default eval run because `eval/run_eval.py` disables live Groq calls to keep routing and tool checks deterministic. The app still uses Groq at runtime when `GROQ_API_KEY` is configured.

## Latest Run

Run:

```powershell
python eval/run_eval.py
```

Then inspect `eval/results/latest_scorecard.md` and `eval/results/results_log.csv`.

## What The Eval Revealed

The most important risk is dependency and environment readiness: without Swiss Ephemeris installed, chart-dependent cases correctly fail. The next most important risk is interpretation depth. The system can ground itself in real chart data and curated notes, but it does not yet have the expressiveness of a carefully prompted LLM composer.

With more time I would:

- Add a separate Groq-enabled eval mode for tone and helpfulness.
- Add a validated LLM-as-judge layer for tone and helpfulness.
- Expand geocoding and cache chart computations.
- Add reference ephemeris fixtures for more exact planetary tolerance checks.
