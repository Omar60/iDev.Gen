"""Eight candidate rooms for candid's look, from Grok, 2026-09-03.

Candid has ONE look and it describes ONE room, and session 368 measured that the
room is the strongest single clause the project has: it arrived identically and
completely in all 25 frames, and it is why eight rows naming a couch built no
couch ([[idevgen-candid-look-owns-the-room]]). So the way to reach a couch is a
catalogue of ROOMS, not more acts — and a new room multiplies the 79 acts that
already exist instead of adding one.

Two defects to hold in mind when reading the screen, both traceable to the
request rather than to the model:

  * **All eight are one sentence with the nouns swapped** — "X sits between her
    and the camera with Y behind her and Z on the floor". The request named that
    shape ("what is between her and the camera") and got eight rewordings of it,
    which is [[idevgen-catalogue-from-a-photograph]]'s finding arriving on
    schedule. Enumerate the forms next time, not the places.
  * **`offers` names furniture the sentence puts out of reach.** `living-room`
    has the sofa BEHIND her and offers "sofa"; `bedroom-day` has the bed between
    her and the camera and offers "bed". A body cannot be on a thing the
    sentence has placed elsewhere. Fixing that is a second pass; this screen asks
    only whether the room is built at all.

`bathroom` had "a towels resting on the floor" and is corrected to "a towel".
Nothing else is edited: the wordings are the candidates.

The capture clause and the hair sentence are NOT here. They are the constant
half of candid's look and every room is spliced behind them, so the arms differ
by the room and by nothing else.
"""

ROOMS = {
    "living-room": (
        "Daylight enters through the side window and leaves the far wall in shadow. "
        "A low coffee table sits between her and the camera while the long sofa "
        "stretches behind her and a rug covers the floor.",
        "sofa"),
    "kitchen": (
        "Overhead fixtures light the counter from above and leave the lower "
        "cabinets in shadow. The stone countertop runs between her and the camera "
        "with open shelves behind her and a stool standing on the tiled floor.",
        "stool"),
    "bathroom": (
        "A wall sconce beside the mirror throws light across the sink and leaves "
        "the far corner dark. The porcelain sink stands between her and the camera "
        "with the framed mirror behind her and a towel resting on the floor.",
        "sink-edge"),
    "shower": (
        "A single overhead fixture lights the stall from above and leaves the "
        "outer corners in shadow. The glass door stands between her and the camera "
        "while the tiled wall rises behind her and the drain sits on the wet floor.",
        "bench"),
    "hallway": (
        "A ceiling bulb near the entrance lights the passage and leaves the far "
        "end in shadow. A narrow console table sits between her and the camera "
        "with the closed door behind her and a runner stretching along the floor.",
        "console"),
    "bedroom-day": (
        "Morning light enters through the open window and leaves the opposite wall "
        "in shadow. The unmade bed lies between her and the camera with a "
        "nightstand behind her and clothes scattered across the floor.",
        "bed"),
    "car-backseat": (
        "Streetlight filters through the rear window and leaves the front seats in "
        "shadow. The wide back seat sits between her and the camera with the front "
        "headrests rising behind her and the floor mats covering the footwell.",
        "backseat"),
    "balcony": (
        "Late daylight reaches the railing from the open side and leaves the inner "
        "wall in shadow. A narrow metal railing stands between her and the camera "
        "with the glass door behind her and potted plants lining the floor.",
        "railing"),
}


# ---------------------------------------------------------------- second pass
#
# Session 370 built all eight rooms, and every one of them put the furniture it
# OFFERS out of the body's reach: the sofa stretching behind her reads as
# background, the bed and the back seat sit between her and the camera so she
# is standing behind them, and the shower offered a bench its sentence never
# mentioned.
#
# The defect is PROXIMITY, not direction. "stretches behind her" is scenery;
# "at her back" is something to sit on. Two rules, applied to all eight:
#
#   * the offered piece is immediately against her -- at her back, under her,
#     at her side -- and never "between her and the camera".
#   * the foreground belongs to a MINOR object she has no need to use, which is
#     what keeps the room's depth without spending it on the furniture.
#
# The light sentence is untouched in all eight: it was not what failed, and
# changing it as well would make this pass unattributable.
ROOMS_FIXED = {
    "living-room": (
        "Daylight enters through the side window and leaves the far wall in shadow. "
        "The long sofa is right at her back with a rug under it, and a low coffee "
        "table with a mug on it stands between her and the camera.",
        "sofa"),
    "kitchen": (
        "Overhead fixtures light the counter from above and leave the lower "
        "cabinets in shadow. A wooden stool stands under her with the stone "
        "countertop and its open shelves at her back, and a bowl sits between her "
        "and the camera.",
        "stool"),
    "bathroom": (
        "A wall sconce beside the mirror throws light across the sink and leaves "
        "the far corner dark. The porcelain sink is directly at her back under the "
        "framed mirror, and a folded towel lies between her and the camera.",
        "sink-edge"),
    "shower": (
        "A single overhead fixture lights the stall from above and leaves the "
        "outer corners in shadow. The tiled wall is right at her back with water "
        "running down it, and the open glass door stands between her and the camera.",
        "tiled-wall"),
    "hallway": (
        "A ceiling bulb near the entrance lights the passage and leaves the far "
        "end in shadow. A narrow console table is directly at her back below a "
        "closed door, and a runner stretches along the floor toward the camera.",
        "console"),
    "bedroom-day": (
        "Morning light enters through the open window and leaves the opposite wall "
        "in shadow. The unmade bed is under her with a nightstand at her side, and "
        "clothes lie scattered across the floor between her and the camera.",
        "bed"),
    "car-backseat": (
        "Streetlight filters through the rear window and leaves the front seats in "
        "shadow. The wide back seat is under her with the door panel at her side, "
        "and the front headrests rise between her and the camera.",
        "backseat"),
    "balcony": (
        "Late daylight reaches the railing from the open side and leaves the inner "
        "wall in shadow. A narrow metal railing is right at her back with the city "
        "beyond it, and potted plants stand between her and the camera.",
        "railing"),
}

# One act per room, naming that room's piece. The SAME act is used for both the
# original and the fixed room, so a pair differs by the room sentence alone.
# Candid's voice, and no line names a foot: the crop reaches the lowest part
# named anywhere ([[idevgen-crop-terms-as-cameras]]).
USE_ACTS = {
    "living-room": "She sits back into the sofa with her knees fallen open and one arm laid along its back.",
    "kitchen": "She sits on the stool with one hand resting on the countertop beside her and the other on her thigh.",
    "bathroom": "She sits up on the edge of the sink with both hands gripping it on either side of her hips.",
    "shower": "She leans back against the tiled wall with her shoulders flat on it and one hand at her neck.",
    "hallway": "She leans back against the console table with both palms flat on its top behind her.",
    "bedroom-day": "She sits on the edge of the bed with both hands pressed into the mattress beside her hips.",
    "car-backseat": "She lies back across the back seat with one arm laid along its top and her knees drawn up.",
    "balcony": "She leans back against the railing with both hands gripping it behind her.",
}

if __name__ == "__main__":
    assert len(ROOMS) == 8
    for key, (text, offers) in ROOMS.items():
        low = text.lower()
        n = len(text.split())
        assert 25 <= n <= 50, (key, n)
        # the room owns the place and nothing else: no person, no camera position,
        # no crop, no mood adjective, no negation
        for banned in (" she ", " her body", "camera angle", "close-up", "cosy",
                       "cozy", "intimate", " not ", " no "):
            assert banned not in f" {low} ", (key, banned)
        assert offers and " " not in offers, key
        print(f"{key:14} {n:2}w  offers={offers}")
    print(f"\n{len(ROOMS)} rooms, all checks pass")
    assert ROOMS_FIXED.keys() == ROOMS.keys() and USE_ACTS.keys() == ROOMS.keys()
    FEET = ("foot", "feet", "heel", "heels", "ankle", "ankles", "toe", "toes")
    for key, (text, offers) in ROOMS_FIXED.items():
        low = text.lower()
        n = len(text.split())
        assert 25 <= n <= 55, (key, n)
        # the whole point of the pass: the offered piece is against her, and it
        # is not the thing standing in the foreground
        assert offers, key
        assert " not " not in f" {low} " and " no " not in f" {low} ", key
        # "backseat" vs "back seat", "sink-edge" vs "sink": compare with spaces
        # and hyphens stripped, so `offers` can be a key and the sentence prose.
        flat = low.replace(" ", "").replace("-", "")
        head = offers.replace("-", "").replace("edge", "")
        assert head in flat, (key, "offers names something the sentence omits")
        # The rule, checked as the rule and not as a phrasing: the offered piece
        # sits against her, and the foreground is something else. Asserting the
        # literal "between her and the camera" would re-impose the very formula
        # that made all eight of the first pass one sentence.
        NEAR = ("atherback", "underher", "atherside", "againsther")
        FORE = ("betweenherandthecamera", "towardthecamera")
        near = [m for m in NEAR if m in flat]
        fore = [m for m in FORE if m in flat]
        assert near, (key, "the offered piece is not placed against her")
        assert fore, (key, "no foreground object at all")
        # the piece must sit nearer the against-her clause than the foreground one
        near_at = min(flat.index(m) for m in near)
        fore_at = min(flat.index(m) for m in fore)
        assert abs(flat.index(head) - near_at) < abs(flat.index(head) - fore_at), \
            (key, "the offered piece reads as foreground, not as something to use")
    for key, act in USE_ACTS.items():
        low = act.lower()
        assert act.startswith("She ") and act.endswith(".")
        assert not [t for t in FEET if f" {t} " in low or low.endswith(f" {t}.")], key
        piece = ROOMS_FIXED[key][1].replace("-", "").replace("edge", "")
        assert piece in low.replace(" ", ""), (key, "act ignores the piece")
    print(f"{len(ROOMS_FIXED)} fixed rooms + {len(USE_ACTS)} acts, all checks pass")
