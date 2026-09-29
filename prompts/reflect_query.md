# Memory reflection query builder (Hindsight reflect)

Given the current incident's causal chain, dependency and triggering change,
Hindsight reflects over retained memories. The query below is what FailureLoop
sends. It asks for pattern resemblance, what worked, and what failed.

Query template:

Reflect on retained incident memories and answer:

1. What recurring failure pattern does this causal chain resemble?
   Current chain: {failure_chain}
   Dependency: {dependency}
   Trigger: {triggering_change}

2. Which fixes succeeded in comparable past incidents, and how many times?

3. Which fixes were tried and did NOT work, and why?

4. How consistent is the evidence (same story everywhere, or conflicting)?

Prioritize incidents with similar causal chains even when error messages
differ. Distinguish evidence from speculation. If memories are thin or
conflicting, say so plainly.
