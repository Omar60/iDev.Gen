# Task 10.3 browser walkthrough

This isolated demo exercises the automatic resource-session journey in the built UI. It creates a temporary source JSON file, temporary configuration and database, an in-process deterministic assistant backed by HTTPX `MockTransport`, and a local ComfyUI double that writes synthetic PNG output. The browser still uses the real backend HTTP API. The assistant transport refuses unexpected endpoints, so the demo makes no provider socket or network calls. It starts with no imported resource rows and never uses a configured provider service, ComfyUI service, GPU, or the repository's real `config.json` or `data/` directory.

## Start

From the repository root, build the frontend and start the demo:

```powershell
npm --prefix frontend run build
.\.venv\Scripts\python.exe scripts\task10_3_browser_demo.py
```

Keep that terminal open. It prints the temporary source file path and serves the isolated app at `http://127.0.0.1:8777/#/resources`; open that URL in a browser. Stop the demo with `Ctrl+C`; temporary files are removed when the server stops.

## Walk through the journey

1. In **Resources**, open **Import Preview**. Use the native file picker to select the exact `invented_rooms.json` path printed in the demo terminal. Click **Upload 1 file(s)**, **Preview Import**, then **Commit Import**. The file declares its invented library key, so no target path or key needs to be typed.
2. Return to **Inventory**. In the imported room's **Translation Mapping**, click **Translate** or **Load editable rows**. Select the two **Suggest translation for …** checkboxes and click **Suggest translations**. Read and, if desired, edit both proposals; click **Preview manual translations**, review the readiness summary, then **Confirm & Apply Translations**. The room becomes ready only after the reviewed translations are applied.
3. Click **Create session** for that ready room. Keep the default **Photo count** of 12, choose **Invented Ada model** as **Character** if it is not already selected, and leave **Brief** blank. Under **Advanced**, confirm **Automatic** authoring, leave both shared overrides empty, and click **Create guided session**. The session opens at **1. Character**.
4. Open **2. Scene / Constants** and click **Generate shared suggestions**. Review both proposals, clear both fields so they remain blank, then click **Accept edited suggestions**. The saved evidence records the reviewed edits and the room remains the only fixed descriptive context.
5. Open **3. Takes** and click **Prepare 12 take(s)**. The local assistant completes takes 001–003 and then returns its one planned error on take 004. Confirm the progress preserves those three takes and exposes **Resume operation**; click it once. The remaining takes finish without repeating completed work.
6. In **3. Takes**, expand **take-004** using **Advanced take fields (camera, framing, pose, expression)**. Change only its **Expression**, then click **Save Draft**. Confirm takes 001–003 remain ready and take-004 onward require preparation. Click **Continue preparation (9)** to prepare only the affected takes. In automatic mode, clearing a descriptive choice leaves it unfilled for the assistant instead of saving an empty choice.
7. Open **4. Review**, inspect the plan and take summaries, then click **Approve Review (Rev …)**. Select only **take-001** and **take-004** in the table. Click **Submit Test Selection (2)**. The generation view should show two pending shots and no completed images.
8. Click **Run (2)**. The local ComfyUI double writes exactly two synthetic PNGs, for the two selected takes.

## Expected boundaries

- The library list is empty before the generated file is imported.
- The demo intercepts only the configured synthetic assistant calls in-process; it does not verify a real provider's socket or network transport.
- No shots exist after guided creation, suggestion acceptance, preparation, or review approval.
- The first preparation attempt records three takes before the deterministic interruption; resume preserves them.
- The room has descriptive inputs, so the conservative conflict detector would emit structural markers beside any non-empty fixed look or wardrobe. This walkthrough accepts edited empty shared choices to stay on the normal reviewed path; it does not claim to exercise expert conflict resolution.
- Editing take-004 carries forward the unaffected first three snapshots and re-prepares only takes 004–012.
- Submitting a selection creates exactly two pending shots; no execution begins until **Run (2)**.
- Running that selection produces only those two shots. The automated regression also asserts zero `FakeComfy` attempts before **Run** and exactly two after it.

For an isolated HTTP regression without browser interaction, run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_task10_3_automatic_journey.py -q
```
