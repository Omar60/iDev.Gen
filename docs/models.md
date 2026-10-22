# Models and defaults

## Inventory

Models keeps the server's order. Each card links directly to `#/model/<id>` and
shows the model name, session count and LoRA filename. The preview uses a fixed,
compact area on desktop and mobile. **No preview** means no LoRA is selected;
**Preview unavailable** means the preview could not be loaded. Neither state
changes the model's settings or determines whether generation is ready.
An empty historical name displays the model ID without changing stored data.

**New model** opens a keyboard-accessible form. The form groups:

- **Character identity:** name, trigger, LoRA and strength.
- **Workflow and generation defaults:** workflow, base model, canvas, sampling.
- **Prompt defaults:** base positive and default negative.

The existing initial defaults remain 832 × 1216, 8 steps, CFG 1 and LoRA
strength 1. Empty base model, sampler and scheduler choices keep the workflow's
own values. The canvas presets remain optional; custom dimensions remain
editable. A saved value absent from the current inventory is shown explicitly
and retained until changed. A save error keeps the entries and focuses its
message so the user can correct or retry.

## Model detail

The summary exposes the full LoRA path, trigger, strength, default workflow,
canvas and session count. **Edit model** uses the same grouped fields. Saving
retains existing notes and other settings not edited by the form. Cancelling
or saving returns focus to the edit action.

Session cards keep their existing order, links and photo counts, with separate
status labels. An unnamed session displays `Session #<id>` without renaming it.
An absent or unavailable cover remains a compact placeholder.

## Two session creation paths

**New session** links to `#/resources/<model-id>` for resource-based setup.
**Legacy session** opens the original manual form separately. It retains its
session kinds, workflow selection, inherited model settings, seed controls,
room/look selection, wardrobe and shot editor. Changing kinds keeps written
prompts. Empty shot rows are omitted when creating a session. Creating a
session does not run generation.

The legacy form is a section, not a submit-on-Enter form: actions inside the
shot editor keep their existing behavior. Its explicit **Create session** and
**Cancel** actions remain available. Cancel returns focus to **Legacy session**.
Deleting a model still requires confirmation naming the model and its sessions.
