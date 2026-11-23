#!/usr/bin/env python3
"""Self-score an agent output file against the sample tickets.

Checks per ticket:
  * action matches outcome.action
  * reply word count <= outcome.max_words
  * every must_mention token appears in the reply (case-insensitive)
  * no forbid_mention token appears in the reply (case-insensitive)
  * output has exactly one line per ticket, valid JSON, in input order

Usage: python3 score.py <tickets.jsonl> <out.jsonl>
"""
import json
import sys


def main(argv):
    if len(argv) != 3:
        print("usage: python3 score.py <tickets.jsonl> <out.jsonl>", file=sys.stderr)
        return 2
    tickets = [json.loads(l) for l in open(argv[1], encoding="utf-8") if l.strip()]
    outs = [json.loads(l) for l in open(argv[2], encoding="utf-8") if l.strip()]
    print(f"tickets: {len(tickets)}  outputs: {len(outs)}")
    if len(tickets) != len(outs):
        print("FAIL: line count mismatch")
        return 1

    n_action = n_words = n_must = n_forbid = 0
    fails = []
    for t, o in zip(tickets, outs):
        tid = t.get("ticket_id")
        out = t.get("outcome") or {}
        reply = o.get("reply", "")
        ok = True

        if o.get("action") == out.get("action"):
            n_action += 1
        else:
            ok = False
            fails.append(f"{tid}: action got {o.get('action')!r}, expected {out.get('action')!r}")

        wc = len(reply.split())
        if wc <= (out.get("max_words") or 10**9):
            n_words += 1
        else:
            ok = False
            fails.append(f"{tid}: {wc} words > max {out.get('max_words')}")

        for tok in (out.get("must_mention") or []):
            if str(tok).lower() not in reply.lower():
                ok = False
                fails.append(f"{tid}: must_mention {tok!r} missing")
            else:
                n_must += 1
        for tok in (out.get("forbid_mention") or []):
            if str(tok).lower() in reply.lower():
                ok = False
                fails.append(f"{tid}: forbid_mention {tok!r} present")
            else:
                n_forbid += 1

        o.setdefault("_ok", ok)

    total_must = sum(len((t.get("outcome") or {}).get("must_mention") or []) for t in tickets)
    total_forbid = sum(len((t.get("outcome") or {}).get("forbid_mention") or []) for t in tickets)
    n = len(tickets)
    print(f"action match : {n_action}/{n} = {100.0 * n_action / n:.0f}%")
    print(f"word limit   : {n_words}/{n} within max_words")
    print(f"must_mention : {n_must}/{total_must} present")
    print(f"forbid_mention: {n_forbid}/{total_forbid} absent")
    bad = {f.split(":")[0] for f in fails}
    print(f"tickets fully clean: {n - len(bad)}/{n}")
    if fails:
        print("--- failures ---")
        for f in fails:
            print(" ", f)
        return 1
    print("ALL CHECKS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
