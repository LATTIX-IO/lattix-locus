# Locus prototype · Increment 3.1 · design stabilization · v0.3.1 · synchronized export

Generated 2026-10-13 18:38. Every file below comes from one working tree; check them against these SHA-256 values.

## How to run

1. Unzip and open `index.html`. Pages work from `file://`. For the iframe-based acceptance tests, serve the folder over http (for example `python -m http.server`) or open Chrome with `--allow-file-access-from-files`.
2. Walk the journey from `home.html`, or open `prototype-tests.html` and press Run all tests (20 checks; your prototype state is saved and restored).
3. State lives in localStorage key `locus.state.v5`. Reset it from the command palette (Ctrl K → Reset prototype sample data).
4. Fonts are packaged in `assets/fonts` (IBM Plex, SIL OFL 1.1). The prototype makes no network requests.

## Load order

Every shell page loads `locus-state.js` (build metadata, shared state, simulator), then `locus.js` (shell, inbox, decision review, session panel, dialogs), then `locus.css`.

## Verification at export time

Note: the original export-time browser results predate the post-review security hardening. Re-run the acceptance suite and store smoke tests on this commit before asserting a current pass. The smoke test now exits nonzero on any failed check.


| Check | Result |
| --- | --- |
| Acceptance suite in headless Chrome (`prototype-tests.html`) | 20 of 20 passed |
| Store checks (`node _qa/store-smoke.js`) | 17 of 17 pass |
| Reflow: 19 pages × [320, 390, 640, 768, 1280] px (640 px = 1280 px at 200% zoom) | 95 of 95 without horizontal overflow |
| Console errors, all pages, light and dark (`node _qa/cdp.mjs errors …`) | no errors (19 pages, light and dark) |
| Network requests at runtime | 0 remote requests |
| Accessibility tree, MVP paths (`node _qa/cdp.mjs a11y …`) | 10 of 10 MVP page states: every interactive element named, landmarks and h1 present, open modals named |
| Screen-reader pass with NVDA or VoiceOver | Not verified |
| Scenario against real services and security test suite | Not verified (see `locus-contract.md` section 7) |

Re-run in the export: `node _qa/cdp.mjs reflow` and `node _qa/cdp.mjs eval prototype-tests.html 1280 "…"` need Node 22+ and Chrome at the default Windows path.

## Files

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `activity-run.html` | 37912 | `7d3d967555a5c79a1bac5c2a44ede71545b53495fa7f9fa9d0b82e6f29b6bc6c` |
| `activity.html` | 7243 | `9cc50186f8a317037cf3502691564b06678e8e870cb0fb20221efd88c930de86` |
| `auth.html` | 14615 | `e75692dfbc579aa5e3aab6f110a94cd1a058cdc5afc7cbdd4a6c88c3aadf1514` |
| `control-tower.html` | 19589 | `ab1bf0284ab8b876642eaee6bbd6147909881dbac955ab98634d367c2256cddc` |
| `home.html` | 9763 | `a949fbe66dbf3dbd660fb333f7721d9d854492f7b95bd5ee34112297743d840f` |
| `index.html` | 13570 | `b5a11a8dabb5b4dffe3c61e7e005cd03c67cd001788836b194c5f97dc231f91f` |
| `library-agent.html` | 23469 | `64bd89f201a357e498da64ad00c908c3c3947f9190b023f83cef13aae3a8c624` |
| `library-team.html` | 17472 | `870aea0b50e418c6e03c6789ba7b7eb969b9689e5d647f7508a7cf19f8a7aca2` |
| `library.html` | 21643 | `aa37fbd039191da9956d8560049ea13820c2269034ee437e0bf3ea401503d725` |
| `memory.html` | 12307 | `a16a51f0d5b691507765661d5749f700a6e579729133a4b65b3df254943e513d` |
| `plan-review.html` | 15351 | `92ff7279c112e0e0793f5dec566346f95b86fb4ee70ec934c8d65c1c38a32d53` |
| `prototype-tests.html` | 26421 | `e3f864e2776c9a8a9a9cb011da514028079c704ad74ab6910ac4f80ea77fbc13` |
| `screen-registry.html` | 29725 | `33e2ec221d81dc4a18f8adf298577f6d23a13920cd6c428e20b1235462dc289d` |
| `settings.html` | 12158 | `da0cff20646155658db86ae5396dc54deec9f45c8bbba0c4ea53d317226d61b2` |
| `task.html` | 12623 | `c49a46a03369ac690c3a7d37ddf10d3ceb79c4932739fb8287771b83484fb20d` |
| `work-conversations.html` | 7920 | `97af01c4bc41acb21f28742ec45ea7a0b9eb2d6fb9895165b4c4cfb7ad75eb8e` |
| `work-projects.html` | 11280 | `4c857251263799da023ca685892a23ffd8a1746e9dde4ed6bbc9682b71d7480e` |
| `work-schedules.html` | 14199 | `b260562deafa93c1b748f995a0911754b6805fb4c48bcd5e516dfd8e8d3b5a6b` |
| `work.html` | 7826 | `cc3e92b5d06ddf1c0b964961ef141920d872afb94119a103dd8448e5d786a406` |
| `locus-state.js` | 97227 | `d2119fe575c21b60df806146f1e71ecc36d78e92525f8feb505ce7613fdccb00` |
| `locus.js` | 40277 | `71f37341b363a78a21ee88a537f89c2cc0dd84ae72bb5b3a8e9005146e6e2405` |
| `locus.css` | 56136 | `ccf9e1bf7601fa23d9dc012b55322c2705bae025f20750cde0d1c73023992572` |
| `locus-contract.md` | 20710 | `033d19693f4f97db842b5f0cfd8b439fd796409700343b6a95c039ff6a318467` |
| `assets/brand/locus-icon-dark.svg` | 902 | `9aef6495aed1c72a2aa749968f18f91f1b440cd0cb0f56a49da0a18eb53394fc` |
| `assets/brand/locus-icon-light.svg` | 902 | `7ac85f4d84357d65571c857575011f18762e43a93369795288c014079e27454a` |
| `assets/brand/locus-logo-dark.svg` | 2106 | `0ee15fa08b881aa9140bea3175eeec811e353370515d7d87554c51fd5cc45505` |
| `assets/brand/locus-logo-light.svg` | 2106 | `04542b98e90b974a3b0b4b722d44fc46ab92852f633cac8652059cce06b0c172` |
| `assets/fonts/OFL-IBM-Plex.txt` | 4429 | `d0283623ef57e722fd0eb688a8041589670c608ab780cd3612d06ba6f153d3fd` |
| `assets/fonts/ibm-plex-mono-latin-400-normal.woff2` | 14708 | `08949f728dc52d528e69b1667d15c89a5686a4ee9a296ff90983985f99c380f7` |
| `assets/fonts/ibm-plex-mono-latin-500-normal.woff2` | 14888 | `01d285447409c8a588692162439a038b8cbd7871309ee20267b0d2d91c6e8e22` |
| `assets/fonts/ibm-plex-mono-latin-600-normal.woff2` | 15620 | `0d1f0b8d0722224e32e9f28261bdc86c79115be73444ae5eceb73976a1bcdf83` |
| `assets/fonts/ibm-plex-sans-latin-400-normal.woff2` | 22588 | `3b646991d30055a93a4ecc499713d4347953a74a947ecab435ab72070cbdab0e` |
| `assets/fonts/ibm-plex-sans-latin-500-normal.woff2` | 24184 | `0717336fb31fcdcde4b8deb3675bb4a0f7f6d484864afcd6751ac29975962203` |
| `assets/fonts/ibm-plex-sans-latin-600-normal.woff2` | 24252 | `8960851d691c054ed38e259bdcf1a6190d157b4203ed5bb32c632a863fb8ec2f` |
| `_qa/cdp.mjs` | 8429 | `0bb58ba921bd6d2b6eac1e1832df70fd0e88c18467b217771a7dd02db578d590` |
| `_qa/store-smoke.js` | 5486 | `edd0f79d29dbe3ae6d7b9346dcc1d958ad74a6625f6924c224ec0934e0d76d72` |
| `_qa/reflow.json` | 19695 | `4441560ea0616b07f8b70fec42d9d901d1d43bcb478c88a3a7cd2fc56c2e5a6f` |
| `_qa/scan_interp.py` | 2057 | `2d05991a685d4cb7f4a7e5b60697661cd45d99ca99442401b03375b638c4f6df` |
| `_qa/harden.py` | 5449 | `5200cf18434157f0a84a4ec9d39b64675b858717ff324e67e7541eb5616a445a` |
| `exports/screenshots-v0.3.1/agent-builder-1280-light.png` | 252474 | `bd6524107d97b581953191c2155cfbc419d0312f5e8bd6c2c1491d975087677c` |
| `exports/screenshots-v0.3.1/control-tower-1280-dark.png` | 134307 | `10e6f7a6bbd8893c8f4fe3254fb0a0f6423b653381122097ae293041ab2d44cf` |
| `exports/screenshots-v0.3.1/control-tower-390-light.png` | 56015 | `e4d5cb71f4fb663f2b42e5a50eb0f4dbe58fc3dd9d4bd9f772cfdc133437f82d` |
| `exports/screenshots-v0.3.1/conversations-1280-light.png` | 157092 | `0033c50cda68ee1eb42aa68edf6553a7cceb637cf040cb8da752ffce659bf285` |
| `exports/screenshots-v0.3.1/decision-review-390-dark.png` | 64784 | `2ebbe80eb0a58804fde0e41310f64cf84162017631e641010e8de8d6be7ee2f9` |
| `exports/screenshots-v0.3.1/home-390-dark.png` | 60917 | `3c0bd18f2ebeefba3d1a4d801f68bd7f5df03a238e1cae38a04be24619d3ede1` |
| `exports/screenshots-v0.3.1/memory-1280-light.png` | 148463 | `3e9d62ab03349c0d38e51de47466ffaeda12549b16d7250c9d78d057a1c7e838` |
| `exports/screenshots-v0.3.1/task-recovering-1280-light.png` | 110865 | `33bae14b0298594b0c11dbaae8c265ac11f360c283c7e07d65e51dd8663a67ce` |
| `exports/screenshots-v0.3.1/team-composer-1280-dark.png` | 213960 | `22dd5a61546a9920d97bc80969ad0cbeb54df7664b9342e01746178d2bc9098b` |
