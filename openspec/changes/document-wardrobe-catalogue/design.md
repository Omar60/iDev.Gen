## Context

See proposal.md - Why. The behaviour is already shipped and green; what is
missing is the contract. That inverts the usual order of this workflow: the
code is the source of truth and the spec is what has to be verified against it,
rather than the other way round.

Two constraints shape how that is done here. First, this repo has found four
times that reading a diff or a docstring finds nothing — a claim about the code
is worth what the probe behind it is worth, and several of the comments beside
the wardrobe describe rules that were later moved (the run-level `bare` flag is
the clearest: its own docstring says it was wrong in both directions). Second,
the wardrobe touches three capabilities that already have specs, so the
boundary matters more than the prose.

## Goals / Non-Goals

**Goals:**

- One `wardrobe` capability whose every requirement was read off a probe of the
  code that implements it.
- The boundary drawn explicitly against `component-matrix`, `shot-composer` and
  `prompt-components`, so the next pre-OpenSpec subsystem written up does not
  overlap this one.

**Non-Goals:**

- Any code change. A requirement that the tree does not implement is a defect
  in the requirement and is rewritten, never turned into an implementation
  task. If a real gap turns up while probing, it is recorded and proposed
  separately.
- The six other pre-OpenSpec subsystems (canvas presets, expression presets,
  the angle picker, depth control, the LLM writer, workflows/models/setup).
  Each is its own change.

## Decisions

**Every requirement is verified by probe, not by reading.** For each one the
verification is a call, a query or a run — read the served catalogue and check
that `covers` is present and matches the crop law's answer; import the shipped
seed twice and compare; derive an arc and count its states. A requirement whose
only evidence is a comment is not verified, because the comment may describe
the rule the code had last month. Alternative considered: write the spec from
the comments, which are unusually thorough here. Rejected — that is how a spec
comes to assert a rule the code stopped keeping, and this repo has already
shipped one stale rule that way.

**The capability boundary is "what she is wearing, and what that entitles a
photograph to".** Concretely: the store, the derivation, the access answer, and
how a session and a take carry a wardrobe. It stops where the trio starts.
Which framings the wardrobe removes from the draw is written here as the
wardrobe's contribution to a constraint that `shot-composer` owns — the crop
law itself stays there, and the requirement here says only that the wardrobe is
part of what the crop law reads and which states count. Alternative considered:
putting the constraint entirely in `shot-composer`. Rejected — the rule that
*every state the run may write* constrains the draw is a wardrobe fact, and
somebody reading only the wardrobe spec would otherwise never learn that a pair
of stockings in the last state costs the whole run its tight crops.

**A garment is not a cell dimension, and the spec says so out loud.** It reads
as a restriction rather than a behaviour, but it is the one line that stops the
obvious "wouldn't the matrix be better keyed on four slots" from being tried
again: it multiplies the matrix by the wardrobe and leaves every measurement in
it unreachable.

**Measured limits are named as measured.** Where a rule exists because of a
result rather than a preference — the empty-string state rendering her
undressed 3/3, the aside stage existing so a `needs: access` act is not a
photograph of a naked woman — the requirement says so. A rule with its
measurement attached survives a rewrite; a bare SHALL invites one.

## Risks / Trade-offs

**The spec is written after the fact and may fossilise an accident as a
contract.** → Each requirement names the reason the rule exists, so a later
reader can tell a decision from an artefact. Where the reason is a known
ceiling rather than a design — a composed arc dresses whatever body the draw
dealt, because the catalogue's acts carry no stage of their own — it is left
out of the spec rather than written as intended behaviour.

**Probing runs against a real installation whose catalogue is the operator's.**
→ Verification uses the test suite's fixtures and the shipped seed, never the
live database, and nothing in this change writes to either.

**Seven subsystems documented one at a time will drift in shape.** → This one
sets the pattern the others follow: purpose, the store, the derivation, the
answer it gives the rest of the app, and the import.
