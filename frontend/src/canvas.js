// The canvases a shoot is painted on — not the sizes a platform asks for.
//
// Those are two different things and only one of them belongs here. The model
// paints about a megapixel; an Instagram post at 1080x1350 or a story at
// 1080x1920 is that megapixel cropped and scaled afterwards, which costs
// nothing. Shooting the delivery size instead means re-running the whole
// session to change a crop — forty frames at twenty-five seconds is nineteen
// minutes per ratio — and the wide ones do not survive the trip: past 16:9,
// with a whole body in frame, the sampler paints two of her.
//
// So five canvases cover every format, and what each one delivers is on the
// option itself.
export const CANVAS_PRESETS = [
  { key: 'portrait', label: 'Portrait (default)', width: 832, height: 1216,
    delivers: 'Pinterest 1000x1500; Instagram 1080x1350 with a sliver cropped' },
  { key: '4:5', label: 'Portrait 4:5', width: 896, height: 1120,
    delivers: 'Instagram portrait 1080x1350, exactly' },
  { key: '1:1', label: 'Square 1:1', width: 1024, height: 1024,
    delivers: 'Instagram 1080x1080, Facebook 1200x1200' },
  { key: '9:16', label: 'Tall 9:16', width: 720, height: 1280,
    delivers: 'Stories, Reels, TikTok 1080x1920, exactly' },
  { key: '16:9', label: 'Wide 16:9', width: 1280, height: 720,
    delivers: 'X 1600x900, Facebook post 1200x630 cropped' },
]

// The width and height boxes stay, so a canvas that is nobody's preset is still
// reachable. This is what the menu shows when they hold one.
export function presetKey(width, height) {
  const hit = CANVAS_PRESETS.find((p) => p.width === width && p.height === height)
  return hit ? hit.key : ''
}
