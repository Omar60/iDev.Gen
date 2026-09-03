"""The crop law, as arithmetic the composer can run.

**The frame reaches the lowest part of the body the line names — anywhere in the
line, in any clause.** A crop clause is not a control; it is more named anatomy,
and a garment counts as the body it covers. Measured 2026-08-28, sessions 313-318
on the empty bench:

* 313 — the six crop terms rendered their own crop 12/12 with NOTHING else in the
  line naming a body part. That is the whole reason the crop belongs here, in a
  composed line, and not in a written shoot where every line walks the whole body.
* 316 — three acts differing only in their lowest named part, each shot with and
  without a crop clause. The ladder is monotone with no crop clause at all, and
  the clause NEGOTIATES rather than commands: it tightens a loose act and loosens
  a tight one, because the word `shoulders` puts shoulders in frame.
* 318 — a garment reaches as low as the body it names. Stockings written down to
  her feet pushed her HEAD out of the frame in 6 renders of 6, and a `waist-up`
  clause rescued 0 of 3.
* 311/312 — the grid that pairs a crop term with a clause asking for a different
  crop. Every cell of it is a contradiction, and this is what nobody could see
  before the law was written down.

So the composer refuses a trio whose framing claims a crop ABOVE something the
rest of the line names. It is not a style rule: the photograph obeys the anatomy,
so the refused cell was never going to measure the framing anyway.
"""
from __future__ import annotations

import re

# The body, top to bottom. The rank is the whole model: a bigger number is lower
# on her, and the frame reaches the biggest number the line names. Deliberately
# coarse — five rungs are what the renders separate, and a sixth would be a
# distinction nobody has shot.
HEAD, CHEST, WAIST, KNEES, FEET = range(5)

PART_NAME = {HEAD: "her head", CHEST: "her chest", WAIST: "her waist",
             KNEES: "her knees", FEET: "her feet"}

# Garments sit on the rung of the body they cover, which is 318's finding stated
# as data: `stockings` is the same word as `thighs` to the sampler.
_BODY = (
    (HEAD,  re.compile(r"\b(head|face|hair|eyes?|eyelids?|mouth|lips|chin|jaw|ears?)\b", re.I)),
    (CHEST, re.compile(r"\b(chest|breasts?|bust|torso|midriff|shoulders?|collarbones?|"
                       r"ribs|stomach|navel|belly|arms?|forearms?|elbows?|hands?|wrists?|"
                       r"bra|bralette|top|shirt|sweater|sweatshirt|hoodie|jumper|pullover|cardigan|camisole|vest|jersey|blouse|corset|harness)\b", re.I)),
    (WAIST, re.compile(r"\b(waist|waistband|hips?|belt|buttocks|backside)\b", re.I)),
    (KNEES, re.compile(r"\b(thighs?|knees?|skirt|dress|shorts|briefs|panties|knickers|thong)\b", re.I)),
    (FEET,  re.compile(r"\b(shins?|calves|calf|ankles?|feet|foot|toes|barefoot|legs?|"
                       r"boots?|heels?|shoes?|socks?|stockings?|fishnets?|tights|"
                       r"trousers|pants|jeans|denim|leggings|joggers|sweatpants)\b", re.I)),
)

# The crop a framing CLAIMS, by the term it uses. These are the six that rendered
# 12/12 alone in session 313, plus the words the catalogue already carries for the
# same six crops.
_CROP_TERMS = (
    (HEAD,  re.compile(r"\b(headshot|head shot|close-?up)\b", re.I)),
    (CHEST, re.compile(r"\b(chest-?up|bust shot)\b", re.I)),
    (WAIST, re.compile(r"\b(waist-?up|mid-? ?shot|medium shot)\b", re.I)),
    (KNEES, re.compile(r"\b(knee-?up|three-quarter|cowboy shot)\b", re.I)),
    (FEET,  re.compile(r"\b(full[- ]body|full[- ]length|full shot|wide shot|"
                       r"extreme wide|long shot)\b", re.I)),
)


def lowest_named(*texts: str) -> int | None:
    """The lowest rung of the body anything in `texts` names, or None.

    None is not `HEAD`: it means the text names no part of her at all, which is
    the state session 313 shot in — and the only state where a crop term is the
    one thing deciding where the edges fall.
    """
    found = None
    for text in texts:
        for rank, pattern in _BODY:
            if pattern.search(text or ""):
                found = rank if found is None else max(found, rank)
    return found


def crop_term(text: str) -> int | None:
    """The crop a wording claims by NAMING a crop term, ignoring the anatomy.

    Separate from `crop_claimed` because the two are used for opposite jobs. The
    anatomy fallback is what makes a framing out of a clause that walks the body;
    it must NOT be used to compare two clauses, because an act naming her chest
    under a `full body` framing is legal (the frame reaches the lowest part, and
    her chest is above her feet) while a second crop TERM at another rung is not.
    """
    for rank, pattern in _CROP_TERMS:
        if pattern.search(text or ""):
            return rank
    return None


def crop_claimed(text: str) -> int | None:
    """The crop a framing wording asks for, or None if it asks for no crop.

    A term wins over the anatomy in the same clause: `a waist-up photograph`
    names her waist either way, and `Top edge cuts above head, bottom edge cuts
    below knees` claims the knees through the anatomy alone. A wording with
    neither is not a crop at all and constrains nothing.
    """
    term = crop_term(text)
    return term if term is not None else lowest_named(text)


def conflict(framing_text: str, *rest: str) -> str | None:
    """Why this line cannot be photographed as its framing says — or None.

    `rest` is every other piece of the composed line: the camera wording, the act
    wording, the wardrobe, and the look when the session uses one. Each one is
    read the same way, because the sampler reads them the same way.
    """
    claimed = crop_claimed(framing_text)
    if claimed is None:
        return None

    # A SECOND crop term, in any other clause, at another rung. The anatomy rule
    # below cannot see this one: `headshot` and `extreme wide shot` name no part
    # of her, so `lowest_named` reads both as None and the line composes saying
    # both at once. Directed's camera catalogue carries fifteen crop terms
    # (`close-up`, `medium shot`, `full body`, `long shot` and their kin) in the
    # CAMERA slot, so the pairing is not hypothetical: it is one draw away.
    # Refused in both directions, unlike the anatomy rule — a looser second term
    # contradicts the framing just as a tighter one does, and either way the cell
    # measures which of two crop words won rather than the framing.
    for text in rest:
        other = crop_term(text)
        if other is not None and other != claimed:
            return (f"the framing asks for a crop at {PART_NAME[claimed]}, and another clause "
                    f"asks for a crop at {PART_NAME[other]}. Two crop terms in one line is not "
                    f"a framing, it is a coin flip between them, and the cell would measure "
                    f"which word won. Draw a camera that claims no crop, or a framing at "
                    f"{PART_NAME[other]}")

    named = lowest_named(*rest)
    if named is None or named <= claimed:
        return None
    return (f"the framing asks for a crop at {PART_NAME[claimed]}, and the rest of the line "
            f"names {PART_NAME[named]}. The frame reaches the lowest part of her the line "
            f"names, so this photograph is cut at {PART_NAME[named]} whatever the framing "
            f"says, and the cell would measure the anatomy rather than the crop. Draw a crop "
            f"no tighter than {PART_NAME[named]}, or an act that names nothing below "
            f"{PART_NAME[claimed]}")
