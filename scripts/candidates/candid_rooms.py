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
