# Task 7.1 Full Auto browser walkthrough

This walkthrough runs the rendered Resources UI against the real local backend and Full Auto services, with synthetic assistant and ComfyUI providers. It verifies the browser-to-backend journey and protocol behavior. The fake ComfyUI writes solid-color 2×2 PNGs, so these images provide no visual-fidelity evidence.

## Start the isolated demo

From the repository root, run:

```powershell
.\.venv\Scripts\python.exe scripts\task7_1_full_auto_browser_demo.py
```

The launcher first builds `frontend/`, then starts the backend on loopback port `8786` and prints the browser URL. Keep its terminal open. If the port is occupied, leave the existing service alone and choose a free port with `--port 8787`; use the URL printed by the launcher.

The app, database, config, and synthetic output files use a disposable temporary directory. Press `Ctrl+C` in the launcher terminal after the run reaches a terminal state. When backend shutdown succeeds, the launcher removes that directory and restores the environment variables it changed. If shutdown exceeds 15 seconds, it preserves the temporary data and reports the timeout.

## Run the journey

Open the printed `/#/resources` URL. In the guided form:

1. Choose **Task 7.1 synthetic character** and **Task 7.1 synthetic portrait room**.
2. Set **Photo count** to `40` and enable **Enable opt-in Full Auto**.
3. Select **Task 7.1 progressive bracelet look**. The catalogue shows latest version `2`; set **Exact saved-look version** to `1` and click **Load exact version**.
4. In **Exact automation profile revision**, choose **Revision 1** and wait for **Server readiness: ready for Full Auto**.
5. Review the consent summary: it should bind look version `1`, profile revision `1`, 40 photos, and the three bracelet stages (worn, set aside, removed).
6. Click **Generate without pauses** once. Do not enter takes or click per-take preparation, approval, submission, or run controls while the backend proceeds.

Wait for the Full Auto run to finish. The expected protocol result is one authorized run, all 40 takes prepared and submitted, 40 completed initial attempts and outputs, all three stages represented, and preparation in two child batches of `[20, 20]` takes. The seeded version `2` is intentionally newer and has no automation profile; using it should not be substituted for the exact eligible version `1`.

## Evidence and boundaries

For independent acceptance, record the visible consent binding and terminal browser progress, and confirm the journey used one Generate action with no intermediate user action. The focused regression `tests/test_full_auto_40_take_journey.py` independently checks the persisted exact look/profile identity, one authorization, all three stage assignments, `[20, 20]` preparation batches, 40 unique take/preparation/shot/attempt/prompt/file links, completed outputs, and queued prompts matching the prepared prompts. A regression result does not replace the browser walkthrough.

Run that regression with:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_full_auto_40_take_journey.py -q
```

The browser uses the actual frontend, FastAPI app, database, Full Auto coordinator, preparation and review/submission services, and Runner. Only the assistant and ComfyUI HTTP endpoints are synthetic: an in-process HTTPX transport returns generated assistant fields and ComfyUI protocol responses, creates 2×2 PNG files, and rejects unexpected hosts. No GPU or external provider is used. The resulting images cannot establish framing, anatomy, garment, or other visual fidelity.
