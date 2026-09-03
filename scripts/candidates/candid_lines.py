"""Grok's 25 candid lines, translated into candid's voice and screened inline.

All 25, duplicates included. The user's argument, and it is right: a different
wording of one geometry is not a duplicate — `standing-hand-in-hair` beat my
`standing-arch-hands-nape` on exactly that basis in session 367.

Translated faithfully. Only two things are corrected, both structural defects
already measured rather than matters of taste:

  * the gaze ("mirada perdida", "mirada baja") is the expression field answering
    the act's question, so it cannot be attributed. Where the line said where her
    FACE points that is kept, because that is head geometry.
  * no line may name a foot: the frame reaches the lowest part named.

Symmetric hand instructions are LEFT IN (`bed-sitting-edge-phone` holds the
phone in both hands) — that rule was measured on directed lines, and whether it
survives a different voice is worth the frame.

The voice is candid's: rows say "She", where directed names the subject. A bare
pronoun has nothing to refer back to in directed's take position; in candid it
is the house voice.
"""

# key -> (wording, needs)
LINES = {
    "couch-phone-scroll": (
        "She sits sunk into a couch looking at her phone in one hand, the other "
        "resting on her thigh, her shoulders loose.", "furniture"),
    "bed-side-looking-away": (
        "She lies on her side on a bed with one arm tucked under her head and the "
        "other hand on her hip, her face turned toward the window.", "furniture"),
    "floor-leaning-wall": (
        "She sits on the floor with her back against the wall and her knees bent "
        "up, one hand in her hair and the other on her belly.", ""),
    "standing-door-frame": (
        "She leans in the doorway with her weight on one hip, one hand sliding up "
        "her neck and the other hanging loose.", "furniture"),
    "bed-stomach-scroll": (
        "She lies on her front on the bed propped on both forearms looking at her "
        "phone, her back lightly arched and her chest lifted.", "furniture"),
    "couch-turned-looking-back": (
        "She sits sideways on the couch with her torso turned to look over her "
        "shoulder, one hand on the backrest and the other on her thigh.", "furniture"),
    "floor-lying-hip-raised": (
        "She lies on her side on the floor with her upper hip raised, one arm "
        "stretched out and the other hand resting at her waist.", ""),
    "standing-window-light": (
        "She stands by the window with her weight shifted, one hand on the frame "
        "and the other sliding down the side of her torso.", "furniture"),
    "bed-sitting-edge-phone": (
        "She sits on the edge of the bed with her torso tipped forward, looking at "
        "her phone held in both hands, her shoulders rounded.", "furniture"),
    "couch-slouched-hand-chest": (
        "She is slumped into the couch with one shoulder lower than the other, one "
        "hand open on her chest and the other on her thigh.", "furniture"),
    "bed-stomach-arched-scroll": (
        "She lies on her front propped on both forearms with her back deeply "
        "arched and her chest pressed into the mattress, looking at her phone.",
        "furniture"),
    "couch-legs-open-phone": (
        "She sits back into the couch with her knees fallen open, one hand holding "
        "her phone and the other high on her inner thigh.", "access"),
    "bed-side-hand-between": (
        "She lies on her side on the bed with her knees half drawn up, one hand "
        "between her thighs and the other under her head.", "access"),
    "floor-sitting-hand-breast": (
        "She sits on the floor against the wall with one hand holding a breast "
        "from underneath and the other on her thigh, her shoulders loose.", "access"),
    "standing-hip-out-hand-waist": (
        "She stands with one hip pushed far out, one hand at her waist pressing it "
        "back and the other sliding up her belly.", ""),
    "bed-back-arm-up-phone": (
        "She lies on her back on the bed with one arm above her head and the other "
        "holding her phone over her chest, her back arched.", "furniture"),
    "couch-leaning-forward-deep": (
        "She sits on the edge of the couch with her torso tipped far forward and "
        "her chest hanging between her thighs, one hand in her hair.", "furniture"),
    "floor-lying-stomach-hand-hip": (
        "She lies on her front on the floor propped on one forearm with the other "
        "hand on her hip, her back arched and her chest lifted.", ""),
    "bed-kneeling-looking-away": (
        "She kneels on the bed with her weight settled over her calves and her "
        "torso turned to one side, one hand on her thigh.", "furniture"),
    "standing-wall-chest-forward": (
        "She stands with her back against the wall and her chest carried far "
        "forward, one hand at her neck and the other sliding down her side.", ""),
    "couch-side-hand-under-breast": (
        "She sits sideways on the couch with one hand lifting a breast from "
        "beneath and the other resting on the backrest.", "access"),
    "bed-stomach-hand-cheek": (
        "She lies on her front propped on both forearms with one hand against her "
        "cheek, her back arched and her hips lightly raised.", "furniture"),
    "floor-sitting-legs-spread": (
        "She sits on the floor against the wall with her knees fallen open, one "
        "hand high on her inner thigh and the other on her chest.", "access"),
    "standing-door-hand-thigh": (
        "She leans in the doorway with her weight on one hip, one hand sliding up "
        "her inner thigh and the other in her hair.", "access"),
    "bed-side-arched-looking-away": (
        "She lies on her side with her back deeply arched and her chest forward, "
        "one hand on her hip and the other under her head, her face turned toward "
        "the window.", "furniture"),
}

FEET = ("foot", "feet", "heel", "heels", "ankle", "ankles", "toe", "toes")

if __name__ == "__main__":
    import collections
    assert len(LINES) == 25, len(LINES)
    for key, (w, needs) in LINES.items():
        low = w.lower()
        assert w.startswith("She ") and w.endswith("."), w
        assert 18 <= len(w.split()) <= 40, (key, len(w.split()))
        assert " not " not in low and " no " not in low, key
        assert not [t for t in FEET if f" {t} " in low or low.endswith(f" {t}.")], key
        for banned in (" left ", " right ", "camera", "lens", "crop", "shot"):
            assert banned not in low, (key, banned)
        assert needs in ("", "furniture", "access"), (key, needs)
    print(f"{len(LINES)} lines, all checks pass")
    print(collections.Counter(n or "-" for _, n in LINES.values()))
    print("phone named:", sum(1 for w, _ in LINES.values() if "phone" in w.lower()))
