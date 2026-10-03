# Task 8.6 browser walkthrough

This walkthrough uses the rendered frontend with an isolated local backend and a deterministic fake assistant. It exercises the existing Resources, guided-session, preparation, and recovery flows; it does not use a real assistant, ComfyUI, the repository `config.json`, or the repository `data/` directory.

## Start the isolated demo

From the repository root, build the frontend bundle:

```powershell
npm --prefix frontend run build
```

Then start the isolated backend and fake assistant:

```powershell
.\.venv\Scripts\python.exe scripts\task8_6_browser_demo.py
```

Keep that terminal open. The launcher creates a temporary config and SQLite database, seeds invented room/model/workflow data, and serves the assistant on loopback. The backend serves the built frontend from the same isolated process, so no Vite server is needed. It refuses to start if backend port `8777` is already in use; leave any service on that port alone and rerun after it has stopped. Press `Ctrl+C` in the launcher terminal to stop only this demo and remove its temporary data.

Open `http://127.0.0.1:8777/#/resources` in a browser.

## Create a synthetic session

In Resources, expand the ready “Invented studio” revision and choose **Create a resource-v1 session draft with this exact revision**. Set **Photo count** to `3`, set the brief to “Two calm portraits in an invented studio,” and in **Advanced** set the look to “Soft daylight in an invented studio” and initial wardrobe to “A white cotton shirt and dark denim trousers.” Keep **Automatic** mode and create the guided session. The page navigates to the new session only after that explicit creation action.

Open the same session URL in a second browser tab before starting preparation. In both tabs, choose **4. Review** and confirm the plan shows three takes and the **Prepare 3 take(s)** action.

In the first tab, click **Prepare 3 take(s)**. Within two seconds, click the same action in the second tab. The first synthetic assistant reply is deliberately delayed so the second tab reaches the backend while the first operation owns the session. The second tab should show the active operation and its saved progress instead of starting duplicate assistant work.

Wait for the synthetic assistant to complete the first take and fail the second. The operation should show `failed`, `Completed: take-001`, `Failed: take-002`, and `Remaining: take-003`. This state is persisted by the backend and remains available after reload.

Click **Resume operation** in either tab. The fake assistant succeeds on subsequent requests. Wait for the operation to reach `succeeded` and for the plan to refresh with all three takes prepared. The previously completed take remains in place while recovery processes the failed and remaining takes.

## Check manual planning and a missing workflow

The isolated database starts with the automatic session above as session `1`. In a PowerShell terminal while the demo stays open, use its saved scene anchor to create a second, manual two-photo draft and save two distinct takes through the compare-and-swap plan API:

```powershell
$api = 'http://127.0.0.1:8777'
$anchorPlan = Invoke-RestMethod "$api/api/sessions/1/plan"
$manualRequest = @{
  request_id = [guid]::NewGuid().ToString()
  character_id = 1
  workflow_id = $null
  scene_anchor = $anchorPlan.plan.authoring.scene_anchor
  photo_count = 2
  brief = 'Two calm portraits in an invented studio.'
  mode = 'manual'
  variation_policy = @{
    camera = @{ mode = 'vary' }
    framing = @{ mode = 'vary' }
    pose = @{ mode = 'vary' }
    expression = @{ mode = 'vary' }
  }
  look = 'A clean invented studio with diffuse daylight.'
  initial_wardrobe = 'A linen shirt and dark trousers.'
}
$manual = Invoke-RestMethod -Method Post -Uri "$api/api/sessions/guided" -ContentType 'application/json' -Body ($manualRequest | ConvertTo-Json -Depth 12)
$sid = $manual.session_id
$draft = Invoke-RestMethod "$api/api/sessions/$sid/plan"
$draft.plan.takes = @(
  @{
    take_id = 'take-001'
    label = 'First invented portrait'
    camera = '50mm eye-level'
    expression = 'calm gaze'
    framing = 'medium portrait'
    pose = 'standing beside a high window'
  },
  @{
    take_id = 'take-002'
    label = 'Second invented portrait'
    camera = '85mm at eye-level'
    expression = 'small smile'
    framing = 'close portrait'
    pose = 'seated beside a studio table'
  }
)
$save = @{ expected_revision = $draft.plan_revision; plan = $draft.plan }
$saved = Invoke-RestMethod -Method Post -Uri "$api/api/sessions/$sid/plan" -ContentType 'application/json' -Body ($save | ConvertTo-Json -Depth 32)
"Manual session $sid saved at plan revision $($saved.plan_revision)."
```

Open the manual session in a browser with `Start-Process "http://127.0.0.1:8777/#/session/$sid"`, choose **4. Review**, and confirm the two distinct takes. Click **Prepare Incomplete Takes (2)**. The UI should show **Batch preparation complete: 2 prepared**, two ready preparations, and a disabled **Prepare Incomplete Takes (0)** action. The manual draft and preparations require no assistant call and do not create shots or run ComfyUI.

To check the missing-workflow remedies, create one more synthetic model without a default workflow:

```powershell
$api = 'http://127.0.0.1:8777'
$workflowlessModel = @{
  name = 'Workflowless invented model'
  lora_name = 'characters/ada.safetensors'
  trigger = '4da woman'
  lora_strength = 1.0
  base_positive = 'portrait photograph'
  base_negative = 'blur'
  workflow_id = $null
  settings = @{ width = 832; height = 1216; steps = 8; cfg = 1.0 }
  notes = ''
}
$workflowless = Invoke-RestMethod -Method Post -Uri "$api/api/models" -ContentType 'application/json' -Body ($workflowlessModel | ConvertTo-Json -Depth 8)
"Created synthetic model $($workflowless.id)."
```

Reload Resources, expand the ready room revision, choose **Create session**, and select **Workflowless invented model**. The form should show **This character has no default workflow**, the **Assign a character default** link and the **choose an Advanced workflow override** remedy, while **Create guided session** stays disabled. Confirm no guided-session POST is sent, the URL remains on Resources, the session list is unchanged, and the new model still has `session_count: 0`.

## Verify the boundary

Use the browser Network panel to confirm the first preparation returns `202`, the competing start returns `409`, and recovery reaches a terminal operation status. The plan read after recovery should contain three completed preparation snapshots. Before leaving the page, inspect the read-only responses for `GET /api/sessions/{id}`, `GET /api/sessions/{id}/plan`, and `GET /api/comfy/status`: the session should have `shots: []` and `running: false`, the plan should have `reviewed_revision: null`, and the isolated demo should report ComfyUI offline at its loopback-only test URL.

Stop at the prepared/review state. Do not click approval, submit, or run controls. Confirm the Network panel contains no `/review/approve`, `/preparations/submit-selected`, or `/run` request. No generation is needed for this walkthrough.
