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
| [`src/idempotency.py`](src/idempotency.py) | What happens when a retry fires twice? | The same action key runs the underlying side effect exactly once. |
| [`src/policy_gate.py`](src/policy_gate.py) | What executes without a human? | Anything not explicitly allow-listed is blocked — default-deny, not default-allow. |
| [`src/event_log.py`](src/event_log.py) | Does state survive a crash? | Replaying the log alone (no in-memory carryover) reconstructs identical state; a tampered entry breaks the hash chain and raises. |

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
pip install pytest
pytest tests/ -v
```

18 tests, no dependencies beyond pytest and the standard library.

## Scope, honestly

This is a demo lab, not a framework — no real LLM call, no real TMS, no
network I/O. The point is the four primitives and the tests that pin down
what they guarantee, not a claim that this is production-ready orchestration
software. `Helix` and `ai-runtime-lab` are further along that road if you
want to see the same ideas taken much further.
