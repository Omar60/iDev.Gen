# Task 10.5 browser walkthrough

This isolated walkthrough uses the built React app and a temporary FastAPI/SQLite instance. It demonstrates compatibility targeting, collection-only import, same-library duplicate accounting, and saving a fused-scene adaptation in the advanced editor. The integrated acceptance test fails staging cleanup on both accesses while staged bytes remain, retries cleanup, and replays the committed result both before and after those bytes are removed while canonical import stays guarded.

## Start the rendered app

Build the frontend and run the local demo:

```powershell
npm --prefix frontend run build
.\.venv\Scripts\python.exe scripts\task10_5_browser_demo.py
```

Open the printed `127.0.0.1` URL. The terminal lists six invented source files. The demo uses a temporary config, database, staging directory and output folder. Assistant requests are rejected, and a fake ComfyUI client is installed. The walkthrough stops before review approval, submission or generation.

## Import historical and collection-only sources

1. On **Resources → Import Preview**, select `historical_rooms.json`, upload it and preview it. Commit the new entry to `task105_historical_rooms`.
2. Start a new selection with `different_declared_key.json`. Its declaration names `task105_new_declared_key`, but the source entry already exists in the historical library. Expand **Advanced Compatibility Options**. Choose `task105_historical_rooms` under **Existing library…**, select **Apply Choice**, and preview again. Confirm the report says one unchanged entry and zero new or updated entries, then commit.
3. Start a selection with `updated_historical_room.json`. Its declared library is the existing `task105_historical_rooms`. Preview and confirm one updated entry, then commit. Confirm the prior revision remains in the library alongside the new revision.
4. Start another selection with `items_only_rooms.json`. It has an `items` collection and no declared library. In Advanced Compatibility Options, enter `task105_items_only`, apply the choice, preview, and commit. The report should show two accepted new entries.
5. Start one selection with both `same_library_a.json` and `same_library_b.json`. Set the target for each file to `task105_shared_duplicate`, applying both choices before preview. Confirm the report shows six inputs, four duplicate occurrences and two accepted new entries. Commit and confirm that only the two unique entries appear in the library.

The demo can be repeated from a clean temporary database by stopping it with Ctrl+C and starting it again. Do not point it at a live service or reuse a port that is already occupied.

## Save and prepare a fused adaptation in the browser

1. In Resources, select **Invented portrait character**. Find the ready library `task105_fused_scenes` and click **Use advanced editor**. The demo seeds this fused revision so its complete description is visible in the resource list.
2. In **2. Scene / Constants**, set **Initial Wardrobe** to `A blue denim jacket.` and save the plan. In **3. Takes**, open **Advanced take fields (camera, framing, pose, expression)** for `take-001`, enter values for all four fields, and save the plan.
3. Open **4. Review**, expand `take-001`, and inspect **Authorized Fused Scene Descriptions**. The original says the subject wears a red dress, so the blue-jacket take has an open structural conflict.
4. Enter `A woman wearing a blue denim jacket stands beside the tall studio window.` in the adapted prompt field and click **Adapt**. Confirm the take conflict moves to **Resolved Adaptations** and the full authorized source description remains displayed unchanged.
5. Click **Prepare Take**. Confirm the take preparation is ready, then inspect **Authoritative Final Prompt** and confirm it uses the saved adaptation. The plan-level conflict counter still shows `1`, Plan Review remains pending, and **Approve Review** and **Proceed to Generation** are disabled. The take-level resolution does not approve the plan. Stop there; approval and generation are outside this walkthrough.

## Automated API and persistence proof

Run the focused integration acceptance:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_task10_5_compatibility_journey.py
```

It exercises the same selection routes and adaptation route used by the UI. It checks exact selected bytes, declared-versus-effective identity, unchanged/new/updated/duplicate accounting against persisted revisions, replay while staged bytes exist and after cleanup removes them with canonical import guarded, and complete equality of canonical library, asset-revision (including coverage), and auxiliary-resource rows. Before adaptation, Prepare Take returns 422 and writes no prepared-take, approval, or shot row. After adaptation, the prepared prompt uses the saved value; no plan approval or shot is created. The test does not use Delete/Restore, network access, an assistant, GPU or ComfyUI.
