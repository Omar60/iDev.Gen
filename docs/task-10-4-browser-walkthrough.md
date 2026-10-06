# Task 10.4 browser walkthrough

This isolated walkthrough demonstrates the manual resource and session path with no assistant, then checks photo extraction in a separate text-only configuration. The app uses temporary configuration, SQLite data, upload staging, and synthetic files. Its assistant HTTP client is replaced with an in-process transport that rejects every request. A local `FakeComfy` is installed as an additional guard; the walkthrough stops before approval, submission, or generation.

## Build and start the no-assistant profile

From the repository root, build the rendered frontend and start the isolated backend:

```powershell
npm --prefix frontend run build
.\.venv\Scripts\python.exe scripts\task10_4_browser_demo.py --profile manual
```

The terminal prints the local URL and the temporary `invented_rooms.json` file to select in the browser. The default port is `8784`. If it is occupied, the script exits without binding; choose another free port with `--port 8785` and use the URL it prints. Do not stop or reuse a service already listening on the requested port.

## Complete the manual journey

1. Open the printed URL with `#/resources`. In **Import Preview**, choose the generated `invented_rooms.json`, upload it, preview it, and commit it. No translation sidecar is included.
2. In **Inventory**, open the room's **Translation Mapping** and choose **Load editable rows**. The source strings are English, but the required identity entries still need review. Keep each translation equal to its source, choose **Preview manual translations**, review the readiness summary, then choose **Confirm & Apply Translations**. The room should become ready without a map file or assistant call.
3. Choose **Create session** for the room. Set **Photo count** to `1`, open **Advanced**, and choose **Manual**. Create the guided session with the seeded **Invented portrait character**.
4. Open **2. Scene / Constants**. Choose **Choose no additional look constraint** and **Choose no initial wardrobe constraint**. These record explicit empty user decisions in the plan while retaining the room's authorized description.
5. Open **3. Takes**. Expand **take-001** with **Advanced take fields (camera, framing, pose, expression)**. Enter a camera, framing, pose, and expression, then choose **Save Draft** and **Prepare Take**.
6. Open **4. Review** and inspect the effective choices, compiled prompt, and manual provenance. Stop here. The session should have no approved review, submitted shots, or generated images.
7. Create a second session for the same room. In **Advanced**, explicitly choose **Automatic** even though no assistant is configured. The draft should load and remain editable. In **3. Takes**, automatic synthesis should be unavailable with a **Configure assistant** action. The integrated regression verifies that the real start endpoint returns `409 assistant_unavailable` without creating an operation or prepared take.

## Check text-only photo handling

Stop the first profile with `Ctrl+C` and wait for its cleanup message. Start a fresh isolated configuration:

```powershell
.\.venv\Scripts\python.exe scripts\task10_4_browser_demo.py --profile text-only
```

Open the printed URL with `#/looks`. Choose **From photo**, then select the generated `invented_look.png` file printed by the terminal. The page should report that extraction is unavailable because no explicit vision model is configured. **Extract look** remains disabled, while **Describe this photo manually** remains available. The generated image contains no private photograph.

The regression also posts the staged image to the real extraction API and verifies `409 vision_unavailable` before the assistant callback, with no saved look, photo evidence, or extraction proposal. The browser run prints the number of rejected outbound assistant requests when it stops; the expected value is `0`.

The integrated manual API regression also saves partially completed manual takes through the real frontend normalization/save helpers. It confirms preparation refuses a missing completion, then exercises both single-take and bulk `manual_completion` routes for the exact empty fields, verifies their persisted manual provenance and effective choices in review, and confirms no assistant call occurs.

## Stop and clean up

Keep the demo terminal open while using the browser. Stop it with `Ctrl+C`. The script shuts down its own backend, closes its temporary database, restores the prior environment variables, and removes its temporary directory only after the backend has stopped. If shutdown times out, it preserves that directory and reports the condition. Each profile starts from a new temporary directory.

For the integrated API and frontend-helper regression, run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_task10_4_manual_journey.py -q
```
