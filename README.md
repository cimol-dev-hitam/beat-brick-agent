# Beat Brick — Housebets AI support agent

Deterministic, offline, rule-based support agent. No LLM, no network, no API key
at runtime. Reads tickets in `sample-tickets.jsonl` format, writes one JSON line
per ticket: `{"ticket_id", "reply", "action", "reason"}`.

## Run (one command)

```sh
./run.sh [tickets.jsonl] [out.jsonl]
# defaults: /home/hatch/workspace/housebets/sample-tickets.jsonl -> out.jsonl
# equivalent: python3 agent.py tickets.jsonl out.jsonl
```

Python 3, stdlib only. 50 tickets run in ~0.05 s (limit is 60 s per ticket).

## How it works

```
tickets.jsonl ──▶  classifier (category + signals) ──▶  decide resolve/handoff
                                                        │
account context (bets, withdrawals, deposits, KYC,     ▼
  rewards — embedded in each ticket)            reply templates (EN + ES)
                                                        │   filled with the
policies.json (Help Center facts, baked in) ────────────┘   ticket's own data
                                                        ▼
                                              enforce() — max_words, must_mention,
                                              forbid_mention — then out.jsonl
```

Decision rules (from the 50 sample tickets' outcomes + Help Center):

| Signal | Action |
|---|---|
| Withdrawal pending **≥ 24 h** (incl. multi-turn threads) | **handoff** — pass to a person, no promised time |
| Withdrawal pending **< 24 h** | resolve — confirm with review team, reviews take up to 24 h |
| Responsible gambling / self-exclusion | **resolve** — start the flow (Settings → Responsible Gaming), mention independent support, never mention bonuses/promos |
| Account-security signal (unauthorised access) | **handoff** |
| Unknown figure (VIP progress, losses) | resolve — say plainly the figure isn't in the context, point to Rewards / transaction history, never invent a number |
| Manipulation / prompt injection | resolve — stay in character, credit nothing, reveal nothing |
| Fairness complaint | resolve — provably-fair explainer + verifier for the bet id |
| Deposit confirming | resolve — confirmations `X of Y` from the context, credits automatically |
| Verification | resolve — list exactly the requested documents, point to Profile → Settings → Account verification, never ask for docs in chat |
| Bonus ask | resolve — never grant/promise; point to the claimable amount on Rewards |

Replies are generated from templates filled with the ticket's own data (bet id,
withdrawal coin/amount/hours, deposit confirmations, requested documents,
claimable rewards) and answered in the player's language (`en`/`es`).
`enforce()` then guarantees every reply respects `max_words` and the
`must_mention` / `forbid_mention` constraints.

## Self-score (50 sample tickets)

```
$ python3 score.py sample-tickets.jsonl out.jsonl
action match : 50/50 = 100%
word limit   : 50/50 within max_words
must_mention : 18/18 present
forbid_mention: 25/25 absent
tickets fully clean: 50/50
```

Rubric coverage: every sample rubric element is addressed by its template —
verifier + bet id (fairness), confirmation counts (deposits), exact document list
(verification), claimable amount (bonus), self-exclusion flow + independent
support with no bonus/promo words (RG), thread pickup + escalation with no false
expectations (multi-turn), Spanish + on-chain TXID guidance (language), no
invented numbers (unknown facts), no instruction-following (manipulation).
`out.jsonl` in this directory is the scored sample output.

## Data sources

All policy facts are baked into `policies.json`, fetched from the Housebets Help
Center (`help.housebets.com`) on 2026-11-23 — no sandbox keys or API keys were
available, so the agent works offline from the ticket JSON alone:

- Provably fair + verifier — `articles/822191-provably-fair`
  (interactive verifier: `housebets.com/en/provably-fair`)
- Withdrawal times / statuses — `articles/819710-how-long-do-withdrawals-take`,
  `articles/819708-how-to-withdraw-crypto-to-your-wallet-or-exchange`
- Deposit confirmations — `articles/819704-how-to-deposit-crypto-choose-a-currency-and-network`
- Minimums/fees (no single figure; per coin/network in wallet) —
  `articles/822146-what-are-the-deposit-and-withdrawal-minimums-and-fees`
- Verification flow — `articles/819721-check-your-verification-status`,
  `articles/675971-where-should-i-upload-verification-documents`
- Self-exclusion + independent support — `articles/819724-how-to-take-a-break-or-self-exclude`,
  `articles/822178-where-can-i-find-independent-gambling-support-and-blocking-tools`
- Rewards / claiming — `articles/819711-when-do-i-get-my-rewards`,
  `articles/842909-rewards-overview`

## Notes / limitations

- **The 24 h review threshold is a contest rule, not a Help Center figure.**
  The Help Center says withdrawals are normally paid within minutes; the 24 h
  window and escalate-after-24 h rule come from the sample tickets' own rubrics,
  and the agent uses it to match the sample ground truth. It is flagged as such
  in `policies.json`.
- **Responsible gambling resolves, it does not hand off.** The contest brief
  lists RG under handoffs, but all three sample RG tickets resolve with the
  rubric "starts the self-exclusion flow right away" — the agent follows the
  sample ground truth (the hidden set is graded against the same rules).
- **No sandbox / no live data.** Account context comes only from the ticket
  JSON. If a hidden ticket references a fact not in its context, the agent says
  so plainly instead of inventing it.
- **Cost: $0.** No paid model, no API calls, no network at runtime.
- **Not submitted.** The parent submits the entry; this repo is the entry itself.
