# Windows desktop Dev updater recovery and pre-merge UAT

**Observed:** Lattix Locus v0.4.0 on Windows shows Dev/Idle and the Check now
button can appear to do nothing. Newer Dev releases *do* exist (v0.10.3,
October 24, 2026), and the rolling `channel-dev/latest.json` asset was
refreshed at that publication.

## Immediate recovery for existing v0.4.0 installations

1. Save any agent/workspace changes and back up the local app state.
2. **Quit using the Windows system tray**. Closing the window just hides the
   Tauri shell; a running backend can prevent in-place replacement.
3. Open
   https://github.com/LATTIX-IO/lattix-locus/releases/tag/dev-v0.10.3
   and download `Lattix-Locus_0.10.3_windows-x86_64-setup.exe` from the
   official release. Confirm the release/tag and package source before running.
4. Run the NSIS installer as an in-place update. Do not uninstall Locus or
   delete the app/agent data directory, and **do not choose Ignore** if the
   installer reports the old app is still running.
5. Relaunch and confirm the displayed desktop version is v0.10.3, the backend
   handshake is healthy, and Settings → Updates is on Dev.

Do not use `lattix update` as a substitute for this process: it is the
source/installer-managed stack updater, not the Tauri desktop updater.

## What LOCUS-473 changes

The native manual check now shares the background install/check busy gate and
emits explicit `checking`, `available`, `up_to_date`, and `error` status
events with an actionable detail. Settings does not start competing silent
checks when its panel and the sidebar mount, does not convert failed signed
feed checks into an "up to date" result, shows progress, and allows retry.
The signer public key, allow-listed Dev/Stable endpoints, and all signature
verification and publication restrictions are unchanged.

A v0.4.0 binary cannot receive these source fixes until replaced by a release
that includes them. This is why the above manual installer is the bootstrap
path when an old shell cannot self-update.

## Pre-merge Windows preview (not the Dev channel)

The desktop **Dev** channel is explicitly main-only. Installing Dev v0.10.3
cannot test unmerged LOCUS-472 / PR #139 code. For a reviewed Windows preview
of a particular PR branch, use the existing manual GitHub Actions workflow:

1. GitHub → Actions → `desktop-release` → Run workflow.
2. Choose the **PR branch** explicitly; do not leave the selector on main.
3. Select `platforms=windows` and `create_release=false`.
4. Enter a valid `MAJOR.MINOR.PATCH` version matching the checked-out
   `VERSION` file. On the October 24 baseline, `0.10.4` is a suitable
   *candidate* only if it has not already been published. Do not choose a
   permanently high version number; its SemVer precedence would block later
   Dev updates.
5. When the job succeeds, download the versioned Windows installer from the
   workflow run's Artifacts. Check the branch and commit SHA in that run first.
   This artifacts-only path does **not** create updater metadata, advance
   `channel-dev`, or grant signing authority to unreviewed PR code. Windows
   Authenticode signing is optional here; do not mistake an unsigned preview
   for a signed Dev channel update.
6. Quit Locus from the tray, back up state, and run the installer in an isolated
   UAT environment. Preserve the verified baseline for rollback.

**Limit:** PR #139's LOCUS-470/C specialist execution is deliberately Linux
stage-only Docker with a nonroot supervisor, independent guardian, and genuine
local Ollama + Codex CLI. A Windows desktop preview *alone* cannot qualify this
workflow. The required real integration acceptance runs on supported Linux
and must pass with zero skips; no pre-merge Windows UI button is wired to that
workflow until the later LOCUS-459 operator journey is implemented.

If Check now still fails on an updated shell, capture the channel, current
version, visible signed-feed error/detail, and the installer release tag. Do not
disable Tauri signature checks or point the update feed to a PR artifact.
