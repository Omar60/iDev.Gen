# Studio dark UI foundations

The first consumer is Setup. Import `frontend/src/ui/foundations.css` and opt in
with a `.studio-ui` wrapper. Every rule and token is scoped to that wrapper;
existing screens, the hash router and the app shell retain their own styles.

Workflows also opts in to these tokens and patterns. Its additional layout and
mapping styles live in `frontend/src/views/Workflows.css`, scoped under
`.studio-ui.workflows-page`, so Setup retains the same foundation rules.

The Sessions list also opts in. Its table/card layout is scoped under
`.studio-ui.sessions-page` in `frontend/src/views/SessionsList.css`; it uses
the existing field, button, state and notice patterns. The single table retains
explicit table roles when styled as mobile cards.

Models inventory and detail opt in with `.studio-ui.models-page` and
`.studio-ui.model-detail-page`. Their compact preview cards, summary and grouped
forms are scoped in `frontend/src/views/Models.css`. `ModelPreview` is reused for
character and session previews, with fixed image dimensions and a visible
fallback. `CanvasSize` accepts optional `studio` and `idPrefix` props for these
forms; callers that omit them keep their previous presentation and behavior.
The shared foundation rules, Sessions list and shell are unchanged.

Library opts in with `.studio-ui.library-page`. Gallery, cover, tag and filter
rules live in `frontend/src/views/Library.css`; long user content wraps without
clamping. Its cover component stays local because the gallery's 3:4 uncropped presentation
differs from the compact portrait previews in Models. Shared foundations and
other views remain unchanged.

## Semantic tokens

| Role | Tokens |
|---|---|
| Surfaces | `--ui-color-canvas`, `--ui-color-surface`, `--ui-color-field`, `--ui-color-info-surface` |
| Boundaries | `--ui-color-border`, `--ui-color-control-border` |
| Content | `--ui-color-text`, `--ui-color-muted` |
| Actions | `--ui-color-accent`, `--ui-color-on-accent` |
| Feedback | `--ui-color-success`, `--ui-color-warning`, `--ui-color-error`, `--ui-color-error-surface` |
| Typography | `--ui-font-family`, `--ui-text-title`, `--ui-text-heading`, `--ui-text-body`, `--ui-text-label`, `--ui-text-help`, `--ui-leading-body`, `--ui-leading-heading` |
| Spacing | `--ui-gap-inline`, `--ui-gap-field`, `--ui-gap-grid`, `--ui-gap-section`, `--ui-padding-surface`, `--ui-padding-control` |
| Shape | `--ui-radius-control`, `--ui-radius-surface` |

The palette keeps the existing dark canvas, panels and blue accent. Body text
uses the system Segoe UI stack at 15 px; page titles use 28 px and section
headings 19 px. Muted text and control borders have separate roles so readable
labels do not depend on a faint divider color.

## Patterns in use

- `.ui-page-header`, `.ui-page-title`, `.ui-lead`: page hierarchy.
- `.ui-surface`, `.ui-section-heading`, `.ui-section-title`: semantic sections.
- `.ui-stack`, `.ui-form-grid`, `.ui-field`, `.ui-field-wide`: layout. The grid
  uses two equal columns and becomes one below 700 px, without implicit columns.
- `.ui-label`, `.ui-control`, `.ui-help`: visible labels and descriptions. Connect
  them with `htmlFor`, `id` and `aria-describedby`; keep meaningful `name` values.
- `.ui-button`, `.ui-button--primary`: secondary and primary actions with 44 px
  minimum height, visible keyboard focus, hover and disabled states. Pending
  Save and Detect actions keep focus with `aria-disabled` and block repeat
  activation in the handler; unavailable actions use native `disabled`.
- `.ui-secret-control`: a password input and an explicitly named Show/Hide button.
- `.ui-notice`: feedback; `data-tone="error"` identifies an error. Setup uses
  `role="alert"` with programmatic error focus and `role="status"` for progress
  and successful actions. Successful saves do not move focus.
- `.ui-state`: text states, with optional `data-tone="success"`, `"warning"`
  or `"error"`. Meaning remains in the text rather than color alone.
- `.ui-definition-list`, `.ui-form-footer`: saved status and the explicit Save action.

Setup keeps the original configuration payload and model discovery behavior.
All auxiliary buttons are `type="button"`; implicit form submission does not
save configuration. Keyboard activation of the Save button still saves it.
Key masking only changes the input type, preserving raw edits, intentional
clearing, discovery requests and the existing `config.json` storage.
