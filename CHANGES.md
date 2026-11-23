# Beat Brick v2 — changes over v1

v1 was submitted 2026-11-23 16:56 WIB as
`https://github.com/cimol-dev-hitam/beat-brick-agent` (self-score 50/50).
v2 is a resubmission candidate: it only replaces v1 if it is genuinely better.
Self-score is unchanged (50/50, all checks pass); every change below targets
the **hidden** set, which has different tickets — tone variety and
generalisation, not sample fitting.

## What changed

### 1. Template variety — tone (20 of 100 pts)
- Every reply template now has 2–3 phrasings, picked deterministically from
  the ticket id (md5 of the id, stable across runs). v1 used one fixed
  paragraph per category, so all 7 fairness tickets read identically.
- Result on the 50 samples: distinct replies per category — fairness 7/7,
  deposit 7/7, bonus 6/6, withdrawal 6/6, verification 4/4, multi-turn 3/3,
  RG 3/3 (manipulation 3/5 and unknown 3/4, bounded by their 3 variants).
- Replies still acknowledge the player's specific situation (bet id, coin /
  amount / hours, confirmations, documents, claimable amount) and never
  invent policy.

### 2. Category normalisation + content inference — generalisation (50 pts)
- Category labels are normalised through an alias map (`withdrawal`,
  `deposit`, `kyc`, `self_exclusion`, `follow_up`, …) instead of exact-match
  only.
- When the label is missing or unknown, the topic is inferred from the
  player's own words (English + Spanish signals). v1 handed off every
  unfamiliar label, which would lose all solved-ticket points on such tickets.

### 3. Content signals checked before the category dispatch
- Responsible-gambling, manipulation and account-security signals (EN + ES)
  are checked first, so those decisions survive label drift — and so the
  reply always matches the action. This fixed a real v2 bug found by the
  robustness tests: an RG-worded ticket labelled `withdrawal_pending` was
  decided as RG-resolve but answered with a withdrawal reply.
- RG signals are gambling-specific phrases only; bare "can't stop" was
  deliberately left out because it false-positives on "can't stop checking
  my withdrawal".
- Manipulation signals are injection/impersonation phrases only ("system
  prompt", "ignore your", "pretend", "grandma", "admin mode", …). "give me"
  and "free spins" are NOT signals: a bonus ask is not manipulation.

### 4. Multi-turn tickets routed by topic
- v1 always answered a multi-turn ticket as a withdrawal. v2 detects the
  thread's topic (withdrawal / deposit / fairness / bonus / verification /
  RG / unknown / manipulation) and answers with the matching template,
  prefixed by a thread-acknowledging lead ("Thanks for bearing with me —
  picking up your case where we left off.").
- The 3 sample multi-turn tickets are all withdrawal threads, so their
  actions are unchanged (handoff, ≥ 24 h).

### 5. Context guards — no invented figures
- Fairness without a bet, bonus without rewards (or $0 rewards), deposit
  without deposit data, withdrawal without `hours_pending`, missing username:
  each now produces a safe reply with no figures instead of a broken one
  (`$None`, `bet  `, `None`, `Hi ,`).
- Withdrawal statuses expanded: `processing`, `in_review`, `review`,
  `manual_review`, `awaiting_review` now count as pending (v1 only knew
  `under_review` / `pending` / `queued` / `on_hold`).

### 6. Portuguese replies
- `pt` tickets get Portuguese replies for the most common templates
  (withdrawal resolve/handoff, fairness, completed-withdrawal language
  pattern). Other categories in `pt` fall back to English.

### 7. Tests
- New `tests/robustness.py`: 56 checks, all pass — reworded intents, aliased /
  missing / unknown labels, Spanish and Portuguese swaps, new security and
  manipulation phrasings, missing context, new withdrawal statuses, multi-turn
  topic routing, no-`outcome` hidden-set shape, empty conversations,
  determinism across runs. Run: `python3 tests/robustness.py`.

## What did NOT change
- Decision core: 24 h withdrawal rule, RG resolves (per sample ground truth,
  not the brief's handoff listing), security handoffs, `enforce()` behaviour.
- `policies.json` (Help Center facts, 17 articles) is unchanged.
- Self-score on the 50 samples: action 50/50, words 50/50, must 18/18,
  forbid 25/25, fully clean 50/50 — identical to v1.
- `run.sh` interface unchanged (`./run.sh [tickets.jsonl] [out.jsonl]`).
  (README.md wrongly described its default as an absolute path on the
  builder's machine; the script itself already defaults to
  `sample-tickets.jsonl` next to it. Only the README text was corrected.)
- Runtime ~0.1 s for 50 tickets; $0 cost; stdlib only; no network at runtime.

## Files
- Changed: `agent.py` (rewritten), `README.md` (v2 notes + fixed defaults),
  `sample-output.jsonl` (regenerated from v2).
- Added: `tests/robustness.py`, `CHANGES.md`.
- Unchanged: `policies.json`, `score.py`, `run.sh`, `sample-tickets.jsonl`.
- Removed: `brick-entry.zip` (stale v1 packaging artifact; the parent re-zips).
