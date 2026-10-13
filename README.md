# QuorumOracle

A reusable multi-source numeric oracle for GenLayer Intelligent Contracts.

A **feed** asks one numeric question ("What is X right now?") and lists 2-5 web sources. Other contracts read the settled value. The LLM does one narrow job (copy a number off a page); everything else is deterministic code, and validators re-derive the whole result independently.

## Why this is a primitive, not a demo

Most "AI oracle" contracts ask one LLM to read one page and trust the answer. That has three failure modes: the model invents a number, one source is wrong or hostile, and validators only check that the output is well-formed. QuorumOracle addresses each:

| Risk | Defence | Where |
|---|---|---|
| LLM invents or misquotes a number | **Grounding**: the number must appear verbatim, as a standalone number, on the fetched page | `is_grounded` |
| One source is wrong, down, or prompt-injected | **Quorum**: the densest cluster of sources must hold at least `min_quorum` sources; outliers are outvoted | `aggregate` |
| Float and locale ambiguity (`1.234,56`) | Strict float-free parser that rejects ambiguous formats | `parse_scaled` |
| Lazy or malicious leader | Validators re-fetch every source, re-run extraction, and re-derive the aggregate | `validator_fn` |
| Leader stores inconsistent per-source evidence | Both sides must report the same source URL and status; when both readings are `OK`, values must match within `tolerance_bps` | `readings_consistent` |
| Leader's stored value differs from what validators derived | **Canonical result**: the settled value is rounded to `sig_digits` significant figures, and validators require the canonical values to be *exactly equal*. No tolerance, so two validators can never accept different values | `canonical_value`, `results_equivalent` |
| Leader reports honest readings but a wrong aggregate | Validators recompute the aggregate from the leader's own readings | `is_well_formed` |
| A bad round corrupts downstream consumers | Only `FINALIZED` rounds update the readable value; failures are logged as evidence | `resolve` |

Grounding and quorum do different jobs. Grounding stops *hallucination* but not *injection*: text such as "report 9,999,999" on a hostile page contains a real number, so it passes grounding. Quorum is what defeats that case (see `test_poisoned_source_is_outvoted`).

## How consensus is used

`resolve(feed_id)` uses a custom leader/validator pair (`gl.vm.run_nondet_unsafe`), following the GenLayer docs' guidance that validators must independently verify rather than check format.

- **Leader**: fetches each source, extracts a number with the LLM, applies grounding and parsing, aggregates deterministically.
- **Validator**, accepting only if all hold:
  1. The leader result is well formed **and its aggregate follows from its own readings**.
  2. The validator's own independent run agrees on **whether a value was finalized**.
  3. For every source, the leader and validator must report the same URL and status. If both statuses are `OK`, their numeric readings must match within `tolerance_bps`.
  4. If finalized, the **canonical values are exactly equal**. There is no drift tolerance: the value the leader stores is the value every accepting validator derived itself.
- Free-text evidence is stored but never compared, because different LLMs will quote differently.
- A round that finalizes stores only the canonical value, never a raw reading. A round that does not finalize stores `0` and the status `NOT_FINALIZED`, and per-source evidence explains why (unreachable, not found, ungrounded, or sources too far apart). A leader that hides a value (reports no value when one exists) or invents one is rejected.
- **Trade-off, stated plainly:** exact equality and matching per-source statuses favour safety over liveness. If a live source moves across a rounding boundary, or a website/LLM produces different source statuses between the leader's and validator's fetches, they disagree and the execution must be retried. Choose `sig_digits` so boundary crossings are rare for your data, and prefer stable, accessible sources.

### Why the canonical result is rounded

Early versions let validators accept a leader value within a configurable drift of their own. That is unsafe: it lets two validators accept materially different numbers, and a downstream contract then acts on whichever one the leader happened to pick. Removing the tolerance and rounding to `sig_digits` significant figures gives one unambiguous, consequential value per round. Consumers should pick `sig_digits` to match the granularity of the decision they make with the number.

## State

```
Feed   owner, question, sources_json, decimals, min_quorum, tolerance_bps,
       sig_digits, round_count, latest_round, latest_value, last_status
Round  status, value, agreeing, reporting, evidence_json   (key: "<feed>#<n>")
```

Values are integers scaled by `10**decimals` (no floats). `latest_*` only ever points at a `FINALIZED` round, and `get_latest` reports `rounds_behind` so consumers can reject stale data.

## API

| Method | Kind | Notes |
|---|---|---|
| `create_feed(feed_id, question, sources, decimals, min_quorum, tolerance_bps, sig_digits)` | write | `sources` = 2-5 URLs separated by spaces or newlines. `min_quorum >= 2`. |
| `set_sources(feed_id, sources)` | write | Feed owner only. Replaces the source list; history is kept because each round stores the URLs it used. |
| `resolve(feed_id)` | write | Anyone can call it. Runs one round. |
| `get_latest(feed_id)` | view | `{value, decimals, round, rounds_behind}`; reverts if nothing finalized yet |
| `get_round(feed_id, n)` | view | status, value, counts, per-source evidence |
| `get_feed(feed_id)` | view | configuration and status |
| `get_feed_count()` | view | |

Parameters: `tolerance_bps` is how far apart sources may be and still count as agreeing. `sig_digits` (1-18) is how many significant figures the settled value is rounded to; it is the precision of the canonical result (a value with fewer digits is stored unchanged). Set `min_quorum` below the number of sources (for example 2 of 3) so one outage does not block finalization.

## Running the tests

```
python -m unittest discover -s tests -v
```

The 71 tests run against `tests/genlayer_stub.py`, a small in-memory stand-in for the SDK with scripted web pages and a scripted LLM. They cover parsing, grounding, aggregation, validator accept/reject cases, per-source status mismatches, input validation, and injection and hallucination scenarios. I also confirmed the suite fails when the validator, grounding check, aggregate consistency check, canonical rounding, exact-equality rule, or last-good-value rule is deliberately broken.

**What the tests do not prove:** they do not run on GenVM, so they cannot show that the contract deploys, that real validators with different LLMs agree, or that real pages extract cleanly. That has to be checked in Studio/Bradbury (below).

## Trying it in Studio / Bradbury

1. Deploy `contracts/quorum_oracle.py` (no constructor arguments).
2. Pick 3 pages that show the number as visible text. Pages are read with `gl.nondet.web.render(url, mode="text")`, which runs JavaScript, so most normal pages work. Sites that block automated requests will not. If a source comes back `UNREACHABLE` or `NOT_FOUND`, check the `chars` field in the evidence: a value equal to the 30,000-character limit means the number was probably cut off.
3. `create_feed` with, for example: a short question, your three URLs, `decimals` = 0, `min_quorum` = 2, `tolerance_bps` = 100, `sig_digits` = 4.
4. `resolve` the feed, then `get_round(feed_id, 1)` to inspect per-source evidence, then `get_latest`.

## Live Studio execution record

The following result was reported for the `demo` feed during a successful live run of the updated contract. The transaction links below were supplied for this run. The links use the GenLayer Studio Explorer; this README does not label the run as Bradbury because the supplied links alone do not establish that network selection.

- **Network / explorer:** GenLayer Studio Explorer
- **Deployment transaction:** [View deployment transaction](https://explorer-studio.genlayer.com/tx/0x36a2bae2f96e8053ad7fa35ce5d8e87fff6e0b086868c36fe6c7c9624ab511d3)
- **Contract address:** [`0x9166AE5c8E596823196A30a79b516685661Fd8c2`](https://explorer-studio.genlayer.com/address/0x9166AE5c8E596823196A30a79b516685661Fd8c2)
- **Successful `resolve` transaction:** [View resolve transaction](https://explorer-studio.genlayer.com/tx/0x80980a6b86aa030798ef39b83d67bef2adcaffd5fb2638a504508b7e9b10632a)
- **Feed:** `demo`
- **Round:** `1`
- **Round status:** `FINALIZED`
- **Canonical value:** `122400000`

The `get_round("demo", 1)` output reported `agreeing = 2`, `reporting = 2`, and `status = FINALIZED`. The per-source evidence was:

| Source | Status | Extracted number | Scaled value | Characters read |
|---|---|---:|---:|---:|
| [StatisticsTimes: Japan population](https://www.statisticstimes.com/demographics/country/japan-population.php) | `OK` | `122,427,731` | `122427731` | 9262 |
| [Worldometer: Japan population](https://www.worldometers.info/world-population/japan-population/) | `OK` | `122,427,731` | `122427731` | 7434 |
| [JetPunk: Japan population](https://www.jetpunk.com/data/population/countries/japan) | `NOT_FOUND` | — | `0` | 16 |

Two usable sources met the configured `min_quorum = 2`. At `sig_digits = 4`, the canonical value was rounded from `122427731` to `122400000`. The subsequent `get_latest("demo")` read returned `decimals = 0`, `round = 1`, `rounds_behind = 0`, and `value = "122400000"`. This confirms that the latest finalized value was current at the time of the read.

**Address verification:** The deployment transaction reports this contract address, and the successful `resolve` transaction lists the same address in its `To` field. Keep both transaction links intact in the submission.

## Known limitations

- `resolve` can be called by anyone, and every call appends a round, so history can grow. Failed rounds never change the value consumers read. A production deployment may want a cooldown or a fee.
- Per-source URL and status must match between leader and validator. If both report `OK`, readings must match within `tolerance_bps`. This strictness improves evidence integrity but can cause retries when a website or LLM behaves differently between fetches. A source that is temporarily reachable by only one side will cause disagreement instead of being silently accepted.
- The settled value is rounded to `sig_digits` significant figures, so it is deliberately less precise than the raw readings. Values near a rounding boundary can cost a retry (see the trade-off above).
- Plain-numeral extraction only: no units, percentages, negatives, or "1.2 million" style figures (the LLM is told to copy the number exactly as printed).
- Only the first 30,000 characters of each page's text are read.
- Pages are fetched with `gl.nondet.web.render` (text mode). If rendering fails, the contract falls back to `gl.nondet.web.get` with HTML tags stripped.
- Sites that block bots (for example Macrotrends) cannot be used as sources.
- Figures split by non-comma separators (`122 427 731`, `122.427.731`) are rejected as `UNPARSEABLE` rather than guessed at.
- The feed owner can replace sources at any time (`set_sources`). That is a trust point: consumers should trust the owner, or the owner should be a contract or multisig that is itself governed.
- The SDK helper calls were written against the current public docs (`run_nondet_unsafe`, `nondet.web.render`, `nondet.web.get`, `nondet.exec_prompt(..., response_format="json")`). Anything that behaves differently on a given Studio/Bradbury version should show up on the first deploy.

## Possible extensions

Per-source reliability weights; an appeal-friendly "challenge round" that forces a re-resolve; a `get_latest_if_fresh(max_rounds_behind)` helper for consumer contracts.
