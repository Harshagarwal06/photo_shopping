# Recognition and reliability redesign

Date: 27 July 2026

## Why

A scenario test of the running app (27 July) found nine defects and a set of
efficiency problems. Three of them share one root cause, and one of them is a
measurement problem rather than a bug:

- The autonomous recognition stage fires a burst of catalogue searches using raw
  OCR text. Blinkit throttles, every search returns empty, and the stage that
  needs catalogue evidence to accept a line receives none. One degraded photo
  took **296 seconds** and skipped 3 of 8 lines for lack of evidence the feature
  itself had destroyed.
- A correct cloud reading cannot win. NVIDIA read a crop as `Rin soap`
  correctly; with no catalogue corroboration the score never cleared the
  threshold and the line was skipped anyway.
- Fixture accuracy is not accuracy. `ocr_fixture_repairs.py` holds 25
  corrections transcribed from four specific photographs. Measured against those
  photographs the pipeline is exact by construction, so no current number
  describes how well the app reads handwriting.
- **The comparison compares different products.** Found later, from a real
  four-item run: the cross-platform matcher short-circuits on the configured
  backend, so each platform is matched in isolation and the resulting baskets
  deliver different amounts. This defeats the app's central claim rather than
  degrading it, and is treated as a first-class section below.

Measured evidence that shaped the design: a single cloud call on the **whole**
photo returned 7 of 8 lines correctly in about 4 seconds with no memorised
repairs involved, while the crop-sheet path produced worse readings
(`Custard puffcorn` for Kurkure Puffcorn). Local recognition itself is not slow —
2.5s on a clean photo, 4.7s degraded. Essentially all of the 296 seconds was the
adjudicator.

## Decisions taken

1. **Local first, automatic full-photo escalation.** Low local confidence sends
   the complete photograph to the cloud model without asking. The previous
   promise — the photo never leaves the machine unless the user presses a button
   — is deliberately replaced by a weaker one, in exchange for the accuracy.
2. **The catalogue-voting adjudicator is deleted**, not repaired.
3. **Evaluation ships against a repairs-off baseline now**, and accepts real
   held-out photographs later.

## Architecture

### Before

```
photo → deskew/contrast → Vision (multi-scale, 5 candidates) → candidate pick
      → 25 memorised repairs → parse → per-line confidence
      → autonomous: N hypotheses × M providers → catalogue voting
        → accept, or silently skip
      → review: transcription checkpoint
```

### After

```
photo → capture-quality gate ─(too poor)→ specific guidance, stop
      → deskew/contrast → Vision → candidate pick → reduced repair set → parse
      → page confidence
      → if low AND cloud reachable: ONE cloud call, FULL photo
      → reconcile local + cloud; quantity disagreement flags the line
      → any line still uncertain → review checkpoint
      → provider search only after recognition has settled
```

Recognition performs no provider searches. That boundary is the point: the
recognition stage reads, the retrieval stage searches, and the two no longer
interleave.

**Page confidence and the escalation trigger.** Page confidence is the fraction
of parsed lines whose per-line confidence reaches `AUTO_SEARCH_CONFIDENCE` (0.8
today), with lines dropped below `MIN_LINE_CONFIDENCE` counted as failures so an
unreadable line cannot raise the score by disappearing. Escalation fires when
that fraction is below 1.0 — that is, whenever any line would otherwise reach the
review checkpoint — and the cloud backend is reachable. One call per photograph,
never per line, and never more than one call per upload.

### Escalation is per page, not per line

`_uncertain_crop_sheet` and the `crop_box` geometry it depends on are removed.
The model uses page layout as context, and the whole-photo call measured better
than the crop sheet on the same image. Crops are retained only for display in
the review checkpoint, where showing the user the handwriting is the point.

### Quantity carries its own confidence

`_semantic_line_quality` scores product words only, so a misread quantity
inherits a confident product name. A degraded photo produced `Cocoa powder` at
**quantity 2** with confidence 1.00, `needs_review` false, and checked by default
in the review UI.

Local and cloud each propose a quantity. Agreement passes. Disagreement flags the
line for review. A quantity nothing corroborates never rides in on a confident
product name.

### `autonomous_safe` stops dropping lines

The policy setting stays; its meaning changes.

| | Today | After |
|---|---|---|
| `review` | Always show the checkpoint | Unchanged |
| `autonomous_safe` | Skip the checkpoint; silently drop unresolved lines | Skip the checkpoint only when every line clears the bar after escalation; otherwise show it |

The behaviour being removed is a line disappearing into a sentence inside a
paragraph. Uncertain lines force the checkpoint; they never vanish.

### Capture-quality gate

Blur (variance of Laplacian), contrast, resolution, and text-line angle are
measured before OCR. Below threshold the app says what to fix — move closer, more
light, hold the phone parallel — instead of manufacturing
`Tate valt puffror 6. Custue` and asking the user to sort it out. Preventing bad
input is the largest accuracy lever that is not the cloud.

**The gate warns; it does not block.** The user may proceed with a poor
photograph and get the normal uncertain-line handling. A hard block would make a
mis-calibrated threshold into a wall, and the thresholds start uncalibrated.
Provisional starting points, to be set from the evaluation harness rather than
guessed in review: Laplacian variance below 100, greyscale standard deviation
below 22 (the value `_prepare_ocr_image` already uses for its contrast boost), a
narrow side below 800 px, or an estimated text angle beyond 8°.

### The repair table shrinks

Once escalation exists the memorised repairs stop being load-bearing. The
evaluation harness measures with them off; whatever escalation already covers is
deleted. Target is fewer than 10 entries, keeping defensible general confusions
(`temato` → `tomato`) and dropping the photograph-specific ones
(`citcat|citat|cicat|citca|itcat` → KitKat).

**Ordering dependency:** no repair is deleted before the harness can measure the
deletion. The harness is therefore built before the table is touched, and each
removal is justified by a measured result rather than by taste.

## Defects

Two of the nine dissolve with the adjudicator and need no separate fix: the
Blinkit self-throttling, and a correct cloud reading being discarded.

### Batch A — parser correctness

| Input | Now | Cause | Fix |
|---|---|---|---|
| `paneer butter masala for 4` | name `…masala for`, qty 4 | `TRAILING_BARE_QUANTITY_RE` consumes a servings count | Reject a bare trailing number preceded by `for`/`serves`; record as servings context |
| `plus dish soap` | name `plus dish soap` | `plus` is not a separator | Add `plus` to the fragment splitter |
| `under ₹800 total` | item `total` | `BUDGET_RE.sub` leaves the trailing word | Strip residual qualifiers after budget removal; drop the fragment if nothing survives |
| `Head and Shoulders shampoo` | query `head & shoulders shampoo` | protected phrases substitute `and` → `&` and never restore | Substitute a non-printing sentinel; restore to `and` after splitting |

The sentinel fix is strictly better than the current `&`: it protects the phrase
from splitting and leaves the provider query clean, where `&` achieves the first
by breaking the second.

The placeholder text itself is also dishonest. `paneer butter masala for 4`
promises dish-to-ingredient expansion that the local backend cannot perform, and
the capability strip already says so. The placeholder will track the configured
backend. Actual dish expansion through the cloud path is out of scope here.

### Batch B — comparison contract

- `_rankable` gains a `matched_items > 0` requirement. A platform that matched
  nothing is not a comparable cart; a fees-only ₹34 "win" is worse than
  declaring no winner. Observed live: Zepto won with 0 of 1 items matched.
- `provider_ids` moves to `Form(default=None)` so "absent" (use the default) is
  distinguishable from "present but empty" (422). Today `""` silently selects all
  three providers and the existing guard is unreachable.
- Unknown provider ids return 422 listing the valid ids, rather than a
  comparison containing zero platforms.

### Batch C — accurate reporting

- **The processing note contradicts itself.** It is assembled by appending
  fragments from three modules, and asserts both "not sent to an external model
  provider" and "sent to Nvidia" in one paragraph. A single builder takes the
  facts — where the photo went, lines read, lines skipped, what needs review —
  and emits one coherent statement. For a feature whose value is trust about what
  left the machine, prose assembled by accident is the wrong mechanism.
- `1 line could not be read clearly and were left out` — a pluralisation helper,
  applied wherever counts are rendered.
- Typed lines display a lexicon-coverage confidence (15% for `quinoa 500g`).
  Typed text is not OCR and should carry no such score into the UI.

## Comparison equivalence

"Which app is cheapest for my list" is the product. It is currently wrong
whenever the compared baskets deliver different amounts, which — for a list
without written quantities — is most of the time.

### The short circuit

`match_across_platforms` carries the correct instruction: *prefer the same brand
and pack size on every platform so their prices are comparable*. That instruction
lives only in the hosted-model prompt, and [matcher.py:400](../../../app/matcher.py)
never reaches it under ordinary configuration:

```python
if settings.demo_mode or settings.safety_lock or settings.model_backend == "local":
    return _fallback_cross_match(item, candidates_by_provider)
```

`_fallback_cross_match` then calls `_fallback_match(item, candidates)` once per
platform. That function has no parameter through which the other platforms could
be known. Three independent "best product for this request" decisions are
rendered side by side as though they were one basket.

The configurations that hit the short circuit — `local` backend, `safety_lock`,
`demo_mode` — are the safe defaults. The equivalence logic is therefore absent
precisely when a cautious user is running the app.

### What it costs, measured

From a live four-item run (basmati rice, rajma, jeera, hing):

| Item | Blinkit | Instamart | Zepto |
|---|---|---|---|
| Rice | Daawat Rozana Super, **Medium Grain** ₹93 | Daawat Rozana Super ₹90 | Daawat Pulav, **Long Grain** ₹148 |
| Rajma | Whole Farm **250 g** ₹48 | Tata Sampann **500 g** ₹93 | Daily Good ₹85 |
| Cumin | Whole Farm ₹41 | Supreme Harvest 100 g ₹49 | Orika Nagauri **jar** ₹167 |
| Hing | Vandevi **Brown** 50 g ₹56 | Everest **Yellow** 50 g ₹85 | Everest 50 g ₹96 |

The rajma row inverts the answer. ₹48 against ₹93 reads as Blinkit at half price;
per 100 g it is ₹19.2 against ₹18.6, so Instamart is cheaper. The cumin row is
not a price difference at all — a ₹41 packet against a ₹167 speciality jar. The
"Blinkit recommended" verdict is partly an artefact of Blinkit's matcher landing
on smaller packs.

### Why the existing safeguards missed it

- **Fill-ratio checking** (`compare.py`) needs a requested measurement to compare
  against. A list without quantities yields "1 item", `requested_measurement`
  returns `None`, and the check cannot run. The four red *"Quantity not verified"*
  lines in that run are the app correctly reporting this; what it does not say is
  that an unverified quantity makes the price comparison meaningless.
- **Ranking** sorts on `coverage_tier` then raw `summary.total`. `per_unit_price`
  exists in `units.py` but only annotates individual fill-ratio warnings.

### Design

> Corrected after implementation. This section first specified matching on the
> **same brand and pack size**. That is wrong, and the existing test
> `test_units_scale_to_the_pack_size_each_platform_stocks` says so: one 1 L pack
> and two 500 ml packs are perfectly comparable. Building what was written here
> would have broken a correct test. The invariant is **delivered quantity** —
> pack size × units — not pack size, and brand is a tiebreak rather than a
> requirement.

**A shared reference amount, with no hosted model required.** The fix belongs in
the fallback, since that is the path that actually runs.

1. **Establish the reference amount.**
   - If the request states a quantity, that is the reference. `unit` defaults to
     `"item"`, and `requested_measurement` reports `(1, "count")` for a line that
     stated nothing, so the default must be treated as *unstated* — otherwise a
     250 g pack and a 500 g pack both appear to satisfy "1 item".
   - Otherwise the catalogue supplies it: of every amount some platform stocks,
     choose the one **the most platforms can actually supply**. Maximising
     coverage is what makes a comparison worth showing; ties break on request
     relevance, then on lower total price.
2. **Hold every platform to it.** For each platform, take the candidate whose
   pack × units lands inside the existing `min_fill_ratio` / `max_fill_ratio`
   band around the reference. Units scale to reach it: two 250 g packs are a
   legitimate way to supply 500 g.
3. **Refuse rather than mislead.** A platform with nothing inside the band
   reports `product_id: null`. Today the fallback always returns something, which
   is how a jar gets compared to a packet.

Candidate ranking within a platform still uses request relevance, **not** the
composite `_score_candidate` value: that normalises its price term against
`lowest_total` within one platform's own candidate list, so composite scores are
not comparable between platforms.

Unmeasurable packs — pieces, loose items, unparseable sizes — fall back to
independent matching. Refusing everything that cannot be measured would reject a
large part of a normal grocery list.

The same check applies to hosted picks. A model can name real, in-stock,
correctly-branded products that deliver different amounts, which is the identical
misleading comparison arriving by a route that looks trustworthy. Delivered
amount joins ids on that trust boundary; a hosted result whose picks disagree on
amount falls back to the deterministic path.

**Ranking needs no new axis.** A platform with no equivalent reports the line as
missing, which drops it a `coverage_tier`, and the existing lexicographic ranking
handles it. This composes with the `matched_items > 0` requirement added in Batch
B: a platform with no comparable products cannot win on an empty cart.

**Disclosure in the interface.** Each comparison row states the amount being
compared and, per platform, the units and pack that supply it — `2 × 250 g` reads
differently from `1 × 500 g` and the user should see which they are buying. A
platform with no equivalent shows that explicitly instead of being absent. Pack
size moves out of truncated dropdown text, where the 250 g / 500 g difference was
invisible, into the row itself.

### Verification

Covered by `tests/test_cross_platform_matcher.py`, driven by the live failure:

- Platforms converge on one delivered amount when an equivalent exists.
- The reported case — 250 g at ₹48 against 500 g at ₹93 — buys two 250 g packs
  for ₹96 against ₹93, so the platform that is cheaper per gram wins.
- A stated quantity overrides the catalogue, even when a larger pack is better
  value.
- A platform that cannot reach the stated amount by any multiple (500 g wanted,
  only a 2 kg sack stocked) reports no equivalent.
- Unmeasurable packs still match independently.
- Hosted picks that disagree on delivered amount are rejected.

## Provider resilience

Once Blinkit began refusing it continued for more than 15 minutes; every query
cost roughly 48 seconds to discover this, including single words like `milk`.

Three causes compound. The adjudicator fires a burst (removed). `blinkit.py`
answers an empty page with three rapid retries — the app amplifying its own
throttling. Nothing remembers the provider is refusing, so query 40 costs what
query 1 cost.

A thin access layer on the search path, where the existing search cache already
sits:

- **Per-provider pacing** — minimum interval between searches to one provider,
  via an async lock and a last-call timestamp. A burst becomes a queue.
  Provisional: 2s.
- **Backoff with jitter** — spaced attempts replace three rapid retries. An empty
  page means "too fast"; retrying immediately is the one certainly wrong reply.
  Provisional: 3 attempts at 2s, 6s, 18s, each with ±25% jitter.
- **Circuit breaker** — after consecutive throttle signals, open for a cooldown
  and fail fast with the remaining time. Provisional: open after 3, cooldown 120s,
  then admit a single trial search before closing.

Every number above is provisional and exists so implementation is not blocked on
a decision. All of them are calibration outputs of a headed run, not review-time
guesses; see the caveat below.

Cart reads and writes stay off this path. It governs `search` only, the same
boundary the cache uses.

### Health that distinguishes connected from working

All three providers reported `connected: true` while Blinkit failed every query,
and the header showed a green "Blinkit connected" throughout. `connected` keeps
meaning "a session exists" and gains a companion signal: last successful search,
and whether the breaker is open. "Connected but not answering" was the true state
for most of the test session and the UI cannot currently express it.

### Streaming the recognition stage

`/api/plans/preview` is a silent POST behind one static line of text, while the
draft stream already narrates itself well. Preview streams its stages the same
way, reusing `StreamEvent`.

The UX gain is secondary. Escalation now sends the whole photograph
automatically, and the user should watch that happen rather than read about it
afterwards. "Local reading was uncertain — sending the photo to NVIDIA…" as a
live stage is a materially different promise from discovering the same fact in a
processing note.

### Calibration caveat

The throttling was not reproduced with a headed browser: the developer's own
server holds the Playwright profile directories, and contending for logged-in
sessions was not acceptable. Headless anti-bot detection may account for some or
all of the behaviour. Pacing, backoff, and the breaker are worth building
regardless — none is harmful against a friendlier provider — but **the specific
thresholds must be calibrated against a headed run** before they are fixed in
place.

## Evaluation

Two sets with different jobs. The four existing fixtures remain regression tests:
pass/fail, do not break what works. The held-out set is a tracked number and
never a gate — it must be allowed to score badly, because that is the
information. Conflating them produces tuning against the benchmark.

Nothing in the held-out set is opened while writing a repair, a lexicon entry, or
a `customWords` term. This cannot be enforced technically, so the tool reports
per-photo scores: one photograph improving while the rest stay flat is the
visible signature of a peek.

| Metric | Question |
|---|---|
| Exact-line accuracy | Is `(search_term, quantity, unit)` right? |
| Spurious items | Did we invent items that are not there? |
| Review rate | How often is the human asked? |
| **Confidently wrong** | **How often are we wrong _and_ unflagged?** |

Confidently wrong is the headline safety number, tracked against a target of
zero. It is what a brand rewrite such as `atta` → `Tata` would trip, and what
`Cocoa powder` at quantity 2 with confidence 1.00 fails today. A confidently
wrong line reaches a cart; a flagged wrong line costs a click. They are different
failures and do not belong in one score.

Three configurations run side by side:

1. local, repairs **on** — today's inflated number
2. local, repairs **off** — the honest local baseline
3. local + cloud escalation — the proposed architecture

Together these answer the question no current measurement can: how much of the
present accuracy is the pipeline and how much is memorisation. They also identify
directly which of the 25 repairs remain load-bearing.

Per-photo latency and whether escalation fired are recorded in the same pass.
Escalation rate drives cost — one cloud call each — so accuracy and cost are
traded against numbers rather than intuition.

`tools/recognition_eval.py`, following the existing `tools/ranking_corpus.py`
pattern, with ground truth as a JSON sidecar per photograph and table plus JSON
output. It is not part of the pytest run: too slow, and it must not gate CI.

It ships against the four existing photographs with repairs off — a real if
narrow baseline, available immediately. Fifteen to twenty-five new photographs
drop into the same harness unchanged.

## Suggested ordering

Two hard dependencies constrain the plan. The harness must exist before any
repair is deleted, and the adjudicator must be removed before provider-resilience
thresholds mean anything (its burst is what the thresholds would otherwise be
calibrated against).

1. Evaluation harness with the three configurations — establishes the baseline
   that every later step is measured against.
2. Delete the adjudicator; escalation becomes per-page and full-photo.
3. Defect batches A, B, C — independent of each other and of the above.
4. Comparison equivalence. Depends on Batch B only for the `matched_items > 0`
   rule it composes with; otherwise independent, and the highest product value
   per unit of work in this document.
5. Provider resilience, calibrated against a headed run.
6. Capture-quality gate, thresholds set from harness output.
7. Shrink the repair table, each removal justified by measurement.

Steps 3 and 4 do not depend on the recognition rework and can be done first if
shipping value early matters more than sequencing cleanly.

## Out of scope

- Dish-to-ingredient expansion through the cloud planner.
- Selector-contract monitoring against saved provider HTML.
- Persisting drafts to SQLite.
- Replacing browser automation with official provider APIs.
