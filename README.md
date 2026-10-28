# agent-verification-lab

[![ci](https://github.com/jarvisss007/agent-verification-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/jarvisss007/agent-verification-lab/actions/workflows/ci.yml)

A small, tested reference implementation of four runtime primitives for
putting a deterministic guardrail around an LLM's decisions: a state guard,
retry-safe execution, a default-deny policy gate, and a hash-chained event
log with replay.

## Why this exists

I built a production system (a broker-inbox-to-TMS pipeline for a drayage
carrier) around the rule that **the model proposes, and something else
decides whether the proposal executes** — no auto-send anywhere, and every
write gets recomputed through a different code path before it counts as
done. That discipline was ad hoc: instinct and code review, not structure.

After looking at how other people building agentic systems name this problem
(`ai-runtime-lab`'s "LLMs are probabilistic, software must be deterministic",
`Helix`'s durable event-sourced orchestration with a default-deny policy
engine), I pulled the same four primitives out into their own small,
independently tested lab — divorced from my employer's code and data,
using a toy freight-triage scenario as the demo.

## The four primitives

| Module | Question it answers | Guarantee proven by its tests |
|---|---|---|
| [`src/fsm.py`](src/fsm.py) | Can the agent skip or reorder steps? | An undeclared transition raises and leaves state untouched. |
| [`src/idempotency.py`](src/idempotency.py) | What happens when a retry fires twice? | The same action key runs the underlying side effect at most once. An intent is recorded before it starts; one that never reports back is marked unknown and handed to a person, never re-run. |
| [`src/policy_gate.py`](src/policy_gate.py) | What executes without a human? | Anything not explicitly allow-listed is blocked — default-deny, not default-allow. |
| [`src/event_log.py`](src/event_log.py) | Does state survive a crash? | Replaying the log alone (no in-memory carryover) reconstructs identical state; a tampered entry breaks the hash chain and raises, and a head file counting every entry ever written catches a log cut short at the end. |

## The demo

[`demo/dispatch_triage_demo.py`](demo/dispatch_triage_demo.py) wires all four
together over a toy scenario: an inbound "broker email" gets classified,
routed through the FSM, checked against the policy gate (drafting a reply
auto-executes, sending an email or writing to a TMS is queued for a human),
executed exactly once even if the same email arrives twice, and logged. The
process is then simulated as crashed and restarted, and the final per-email
status is rebuilt purely by replaying the log.

Run it directly:

```bash
python demo/dispatch_triage_demo.py
```

## Tests

```bash
pip install pytest hypothesis
pytest tests/ -v
```

The primitives need only the standard library; the tests need pytest and Hypothesis.

- 19 unit tests pin down each primitive's intended behaviour.
- [`tests/test_scenarios.py`](tests/test_scenarios.py) runs 102 adversarial
  scenarios in six fault families: what the model proposes, malformed model
  output, every state/event pair, duplicate deliveries and lost
  acknowledgements, a crash at each step, and a tampered log.
- [`tests/test_properties.py`](tests/test_properties.py) checks four
  properties with Hypothesis over generated inputs.
- [`tests/baseline.py`](tests/baseline.py) reruns the scenarios with the
  guardrails switched off, for the table below.

## Guardrails on vs off

A guardrail claim only counts if you can see the difference it makes. Each
of the four guardrails can be swapped for a pass-through stand-in that lets
everything through. The stand-ins are test-only
([`tests/bypass.py`](tests/bypass.py), switched on by the `AVL_BYPASS`
environment variable); nothing under `src/` changes. The table counts the
scenarios whose invariant failed, run once with every guardrail on, once with
each one off, and once with all four off. Rebuild it with:

```bash
python tests/baseline.py --write
```

<!-- baseline:start -->
| Scenario family | Scenarios | On | FSM off | Idempotency off | Policy off | Log off | All off |
|---|---:|---:|---:|---:|---:|---:|---:|
| What the model proposes vs what runs | 14 | 10 | 10 | 10 | 12 | 10 | 12 |
| Malformed model output | 6 | 0 | 0 | 0 | 6 | 0 | 6 |
| Every state/event pair | 25 | 0 | 21 | 0 | 0 | 0 | 21 |
| Duplicate deliveries and lost acks | 24 | 0 | 0 | 20 | 0 | 0 | 20 |
| A crash at each step, then restart | 10 | 0 | 0 | 4 | 0 | 0 | 4 |
| A tampered log | 23 | 7 | 7 | 7 | 7 | 23 | 23 |
| **Total** | **102** | **17** | **38** | **41** | **25** | **33** | **86** |
<!-- baseline:end -->

How to read it:

- **The On column is not zero.** Those 17 are the open defects listed below,
  counted as the violations they are rather than hidden as expected failures.
  CI checks that the On column matches the strict `xfail` markers exactly, and
  that this table matches the code.
- **A guardrail shows up only where a scenario exercises it.** The FSM matters
  only in the state/event family, because the demo pipeline never tries to
  skip a step; idempotency only where a delivery repeats.
- **This is bounded test evidence, not a universal guarantee.** It shows what
  the guardrails stop in these 102 scenarios, written by the same project that
  wrote the guardrails. It says nothing about scenarios nobody wrote, real
  model output, concurrent processes, or an attacker who can change the code
  as well as the log.

## Known defects

Found on 2026-10-27 by the scenario suite. In these cases the guarantees in
the table above do not yet hold. Each scenario that hits one is a strict
`xfail`: CI stays green while the defect is open and goes red if a scenario
starts passing before its marker comes off. The code as first published is
tagged [`v0.1`](https://github.com/jarvisss007/agent-verification-lab/tree/v0.1).

| ID | What goes wrong | Scenarios |
|---|---|---|
| D4 | An edited log whose hashes were recomputed replays cleanly | 4 |
| D5 | A tampered log can be reopened and appended to | 3 |
| D6 | An action outside the allow-list is queued for a human instead of refused | 10 |

Fixed:

- **D1** (2026-10-27): a retry after a restart re-ran a side effect that had
  already run, because the executor kept its record of completed work only in
  memory. That record is now rebuilt from the event log on restart
  (`boot()` in the demo, `IdempotentExecutor.restore`). 6 scenarios.
- **D2** (2026-10-27): a side effect that landed but never reported back (a
  lost acknowledgement, or a crash before its result was logged) ran again on
  the retry, because nothing said it had started. Each run is now an
  intent/done pair: the intent goes into the log before the side effect
  starts. A retry that finds an intent with no result raises `OutcomeUnknown`
  instead of running it, and the demo records the email as
  `outcome_unknown_needs_human` for a person to check. The cost is the other
  side of at-most-once: a crash between the intent and the side effect also
  reads as unknown, so a person sometimes checks an effect that never ran.
  10 scenarios.
- **D3** (2026-10-27): deleting the newest entries went unnoticed, because a
  hash chain cannot see its own end: every entry left still points correctly
  at the one before it. The log now keeps a head file beside it that counts
  every entry ever written, updated after each append. Replay refuses a log
  shorter than its head, and a log with entries but no head. Reopening a cut
  log does not reset the count. The head is a second file on the same disk,
  so someone who can rewrite both files and recompute the hashes still gets
  through; that is D4. 4 scenarios.

## Scope, honestly

This is a demo lab, not a framework — no real LLM call, no real TMS, no
network I/O. The point is the four primitives and the tests that pin down
what they guarantee, not a claim that this is production-ready orchestration
software. `Helix` and `ai-runtime-lab` are further along that road if you
want to see the same ideas taken much further.
