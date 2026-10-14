# Full Auto rollback

Use this procedure to start a recorded older source release with a restored
pre-upgrade database. Older releases do not understand the Full Auto schema.
An in-place downgrade is unsupported, and `PRAGMA user_version` cannot stop old
code from writing. The preflight never launches the older release.

## Before upgrading

1. Record `git rev-parse HEAD`, the source/release provenance, and the effective
   config and data locations, including any `IDEVGEN_CONFIG` or
   `IDEVGEN_DATA_DIR` overrides. Keep that source revision available in its own
   checkout.
2. Before deploying the upgrade, while the pre-upgrade backend is still
   running, use its source root and the same environment to create a consistent
   database backup outside the data directory:

   ```bash
   python scripts/backup_db.py --target ../rollback/pre-upgrade.db --json
   ```

   Continue only when the report says `"status": "ok"`, and retain its reported
   target. The backup uses SQLite's online backup API and includes committed
   WAL state. `../rollback/` is a relative placeholder resolved from the source
   root and must be outside the data directory. The backup contains no
   photograph files.
3. Separately preserve byte-for-byte copies of the pre-upgrade config and the
   matching `<data>/sessions/` photos. Keep these, the database backup, and the
   older source checkout outside the upgraded data location.

## Before starting the older release

1. Cancel or reconcile upgraded work and wait until Full Auto runs, attempts,
   authoring operations, and sessions are settled. Stop every upgraded backend
   process and wait for shutdown. The preflight checks persisted work state but
   cannot prove that all processes have stopped.
2. Retain the upgraded database and images intact. In a fresh, disjoint restored
   data directory, copy the backup byte-for-byte to `idevgen.db` and copy the
   saved photos to its `sessions/` directory. Put the saved config at a separate
   path. Use independent file copies; do not link restored files or directories
   to upgraded evidence.
3. Keep the upgraded checkout that contains `backend.full_auto_rollback` and the
   separate checkout at the recorded older source revision. The preflight module
   is run from the upgraded checkout; `--runtime-root` points to the older
   checkout. Set both environment variables in the shell that will run the
   preflight and then start the older release. Resolve these path variables to
   absolute paths on the machine:

   - `PRE_UPGRADE_BACKUP`: the saved database backup
   - `UPGRADED_DATA`: the retained upgraded data directory
   - `RESTORED_DATA`: the separate restored data directory
   - `RESTORED_CONFIG`: the copied pre-upgrade config
   - `OLDER_SOURCE_ROOT`: the checkout at the recorded old revision

   In either shell, replace each quoted placeholder with its absolute path.
   For POSIX shells, for example:

   ```bash
   PRE_UPGRADE_BACKUP='<absolute path to backup>'
   UPGRADED_DATA='<absolute path to upgraded data>'
   RESTORED_DATA='<absolute path to restored data>'
   RESTORED_CONFIG='<absolute path to restored config>'
   OLDER_SOURCE_ROOT='<absolute path to older source checkout>'
   ```

   In PowerShell, assign the corresponding absolute paths:

   ```powershell
   $PRE_UPGRADE_BACKUP = '<absolute path to backup>'
   $UPGRADED_DATA = '<absolute path to upgraded data>'
   $RESTORED_DATA = '<absolute path to restored data>'
   $RESTORED_CONFIG = '<absolute path to restored config>'
   $OLDER_SOURCE_ROOT = '<absolute path to older source checkout>'
   ```

   For POSIX shells:

   ```bash
   export IDEVGEN_CONFIG="$RESTORED_CONFIG"
   export IDEVGEN_DATA_DIR="$RESTORED_DATA"
   ```

   In PowerShell, use the equivalent assignments after setting the path
   variables above:

   ```powershell
   $env:IDEVGEN_CONFIG = $RESTORED_CONFIG
   $env:IDEVGEN_DATA_DIR = $RESTORED_DATA
   ```

   Run the preflight from the upgraded checkout, with absolute paths for all
   inputs. This keeps its module import root separate from the old runtime root
   and avoids changing the meaning of environment paths when launching the old
   release:

   ```bash
   python -m backend.full_auto_rollback --check --backup "$PRE_UPGRADE_BACKUP" --upgraded-data "$UPGRADED_DATA" --restored-data "$RESTORED_DATA" --config "$RESTORED_CONFIG" --runtime-root "$OLDER_SOURCE_ROOT"
   ```

   A nonzero exit means stop: do not start the older release. A zero exit only
   means the listed preconditions passed. Start the older release with its
   normal deployment command from `OLDER_SOURCE_ROOT`, using this same shell
   and environment. If either environment path is relative, it resolves from
   the old runtime root during preflight and from the old checkout's launch
   working directory when the app starts; absolute paths avoid that dependency.
   The preflight command never starts an executable.

The preflight inspects all three databases through private temporary copies for
integrity and foreign keys. It also requires a pre-Full-Auto backup, byte
identity between backup and restored database, and settled upgraded work. The
upgraded copy includes its WAL; hashes before copying, of the copy, and after
copying must match. Supplied files are not modified. The filesystem walk rejects
symlinks, junctions, aliased trees, and shared file identity with upgraded
evidence, including config hard links. It does not compare photo contents,
verify photo completeness or visual correctness, authenticate backup provenance
or creation time, or prove every upgraded process has stopped. Record and verify
those operator-controlled facts separately. This procedure does not verify real
ComfyUI generation or image quality.
