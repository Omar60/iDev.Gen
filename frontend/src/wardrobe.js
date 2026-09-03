// The wardrobe catalogue, and the undressing derived from it.
//
// Same shape as `compose.js` and `judge.js`: pure functions, no React, no fetch,
// so the derivation is a test surface rather than something only a screen can
// show. The store is loaded once by the app and read from here.
//
// The one fact this file exists to keep: a wardrobe state names ONLY what she is
// still wearing. Written the other way round — "The leggings are off" — the
// garment's word is in the composed line, `backend/crop.py` reads it as her
// feet, and every framing above the knee leaves the draw's pool for the whole
// run (a 422 with no photograph in it). It cost a shoot on 2026-09-02 and it was
// a note somebody had to remember; states built from the garments she has LEFT
// cannot express it.

let _garments = []
let _outfits = []

export const setWardrobe = (data) => {
  _garments = Array.isArray(data?.garments) ? data.garments : []
  _outfits = Array.isArray(data?.outfits) ? data.outfits : []
}

export const garments = () => _garments
export const outfits = () => _outfits

/** An outfit's garment keys, in the order they come off. The API serves the
 *  column as a string; a list is accepted so a seed row read straight off disk
 *  works, the same both-spellings rule `arrangements()` keeps for `cameras`. */
export const garmentKeys = (outfit) => {
  const value = outfit?.garments
  const list = Array.isArray(value) ? value : String(value || '').split(',')
  return list.map((k) => String(k).trim()).filter(Boolean)
}

/** The wardrobe as one sentence, from the garments she is wearing.
 *
 *  Nothing left is "She wears nothing at all." and not an empty string: an empty
 *  wardrobe is a line that says nothing about clothing, and a line that says
 *  nothing about clothing renders her undressed by accident rather than by the
 *  arc reaching its end. Measured 2026-08-31, nude 3/3 with no reference
 *  attached. The sentence says it on purpose.
 */
export const wearing = (wordings) => {
  const worn = wordings.filter((w) => w && w.trim())
  if (!worn.length) return 'She wears nothing at all.'
  if (worn.length === 1) return `She wears ${worn[0]}.`
  return `She wears ${worn.slice(0, -1).join(', ')}, and ${worn[worn.length - 1]}.`
}

/** The arc of an outfit: N garments derive N+1 states, one garment coming off at
 *  each step, in the order the outfit lists them.
 *
 *  The last state is bare. That is one of the two stages a `needs: access` act
 *  can be photographed in — the other is the aside stage — and it is why the
 *  order is authored on the outfit rather than dealt:
 *  a shoot that takes the sweatshirt off before the knickers is a different
 *  shoot from one that does not, and neither is the composer's guess to make.
 *
 *  An unknown garment key is dropped rather than rendered as its key: the key is
 *  an identifier ('grey-sweatshirt'), and an identifier in a prompt is two words
 *  the sampler will happily paint.
 *
 *  A garment with an `aside` wording adds one more stage while she still has it
 *  on — see the comment on the branch below.
 */
export const arcFor = (outfitKey) => {
  const outfit = _outfits.find((o) => o.key === outfitKey)
  if (!outfit) return []
  const byKey = new Map(_garments.map((g) => [g.key, g]))
  const keys = garmentKeys(outfit).filter((k) => byKey.has(k))
  const states = []
  for (let i = 0; i <= keys.length; i += 1) {
    const left = keys.slice(i)
    // `access` is the question the composer asks of a stage: can a hand or a toy
    // reach her in this photograph. Nothing covering her below the waist answers
    // yes, and so does the aside stage below — that is what it is for. `covers`
    // is served by `/api/wardrobe` off `crop.lowest_named`, so the ladder that
    // decides it is the same one the crop law reads the composed line with.
    states.push({
      text: wearing(left.map((k) => byKey.get(k).wording)),
      access: !left.some((k) => byKey.get(k).covers),
    })
    // The garment moved out of the way, as its own stage, when it is the last
    // thing she has on. An act with a toy in it does not need her undressed — it
    // needs access, and a garment that can be pulled aside gives access while she
    // is still wearing it. Without this stage the arc goes from `knickers` to
    // `nothing at all` and every such photograph is of a naked woman.
    //
    // ponytail: only when it is the LAST garment left, so an outfit gains at most
    // one of these and it lands where anyone would put it. A skirt pushed up over
    // a pair of knickers is the same idea two layers up; if that shoot is ever
    // wanted, the rule to relax is this `length === 1`, not the data.
    if (left.length === 1 && byKey.get(left[0]).aside) {
      states.push({ text: wearing([byKey.get(left[0]).aside]), access: true })
    }
  }
  return states
}

/** The arc as the plain sentences, which is what a composed line carries. */
export const statesFor = (outfitKey) => arcFor(outfitKey).map((s) => s.text)
