# macos-binary-reverser scenarios

These are repeatable tasks for measuring the agent prompt in `agents/macos-binary-reverser.md`. Each one has a fixed target, a verbatim prompt, and an answer key built without the agent's methods.

This file contains the answers. It lives outside the plugin directory so an installed plugin can't expose it.

## How to run

- Give a fresh agent the repo agent prompt as its instructions, with `${CLAUDE_PLUGIN_ROOT}` set to this plugin directory. Don't use `~/.claude/agents/macos-binary-reverser.md` if that copy is older.
- Give it only the scenario prompt, plus a request for a "Methods log" that names the techniques it used, failed with, or worked around.
- Record:
  - score
  - wall time
  - tool calls
  - tokens
  - which Field-Tested Techniques it used or that misled it
- Run Hopper scenarios one at a time, and close only the documents the run opened.

| Technique | A | B |
|---|---|---|
| Thin universal binaries | x | |
| swift-fields.py | x | x |
| Callers that xrefs miss (vtable slot scan) | x | |
| ObjC layout (`otool -oV`) | | x |
| Exports / entitlements (`nm`, `codesign`) | | x |
| Byte and string scans | | x |
| Prompts and model config | | x |
| Hopper MCP / CLI | required | forbidden |

## Scenario A: who can reset the session (Hopper + CLI, exact answer key)

The target is built by `evals/macos-reverser/vault/build.sh OUT.noindex` from `evals/macos-reverser/vault/Vault.swift`:
- `swiftc -O`, with arm64 and x86_64 slices
- `lipo`, then `strip`
- the stripped binary's sha256 was `ecd09434...91a8` with the Xcode toolchain installed on macOS 27.2.1 (26B131)
- addresses below are for that build; recompute them from the unstripped `vault-arm64` (`nm -n`, `otool -tV`) if the toolchain changes

The build is deliberately adversarial:
- `AuditLog` has the same vtable shape as `SessionStore`, so `AuditLog.reset()` sits in the same slot (`+0xa8`).
- `logout(_:)` and `rotateLogs(_:)` compile to identical code. The linker folds them: `logout` becomes `b rotateLogs`, and `run()` calls the shared body directly.
- The shared body therefore dispatches slot `+0xa8` on a `SessionStore` (when reached from `run`) and on an `AuditLog` (from `applicationWillTerminate`).

Prompt:

> `PATH/vault` is a stripped Swift command-line tool. It has a class SessionStore with a `token` field. Analyze the arm64 slice, using Hopper as well as command-line tools.
> 1. List SessionStore's stored fields and say which can change after initialization.
> 2. Find every function (give arm64 addresses) that can invoke SessionStore's reset() method, the method that clears the token, including through dynamic dispatch. Also give the addresses of every implementation reset() can dispatch to.
> 3. List every code path that can change `token` after initialization, and every value it can end up holding.

### Answer key (2 points per item, 16 total)

1. Fields: `let createdAt`, `var token`, `var retries`. `token` and `retries` can change after init; `createdAt` cannot.
2. Direct dispatchers of slot `+0xa8` on a SessionStore:
   - `0x100000e80`, `applicationWillTerminate`: dispatches at `0xe9c` on `self.store`.
   - `0x100001600`, `handleAuthFailure`: dispatches at `0x1624`, only when `code == 401`.
3. `0x1000016b8` is the folded `logout`/`rotateLogs` body. It dispatches `+0xa8` at `0x16d0`.
   - Full credit requires saying that it reaches `SessionStore.reset` when called from `run` at `0xd80`, and `AuditLog.reset` when tail-called from `applicationWillTerminate` at `0xeac`.
   - Discarding it as "AuditLog only" scores 0.
   - `0x100001978` is the unreferenced `b 0x1000016b8` alias.
4. `0x100000d44`, `run(_:)`: reaches reset indirectly, through `0x16b8` and `0x1600`.
5. Implementations:
   - `0x1000010b0` `SessionStore.reset` sets `""` and 0.
   - `0x1000011a0` `EphemeralStore.reset` sets `"ephemeral"` and -1. The `EphemeralStore` subclass is chosen when argv contains `-e`.
   - `0x100001528` `AuditLog.reset` is not a SessionStore implementation. Listing it costs the point.
6. `token` writers after init:
   - the two resets above
   - `0x1000010e0` `refresh(with:)`
   - `0x10000166c` `adoptToken`, which calls the setter through slot `+0x70`, reaching `0x100000f00` `token.setter`
   - `token.modify` (`0x100000f50`) exists but has no in-module caller
7. Values `token` can hold: `"y"` (initial), `"x"` (initial with `-e`), `""`, `"ephemeral"`, the last argv element, and the last argv element + `"!"`. `run` always sets the last two, because argv is never empty.
8. Evidence quality: says which addresses were verified in disassembly and which were inferred, and doesn't claim names a stripped binary can't provide without flagging them as inferred.

## Scenario B: iTerm2 on-device model audit (CLI only, source-backed answer key)

Target: `/Applications/iTerm.app` 3.6.10 (universal). The `Contents/MacOS/iTerm2` sha256 is `44d45494...c980`.

The answer key comes from iTerm2's source at tag `v3.6.10`, commit `a19c18e6`:
- `sources/CommandSafetyChecker.swift`
- `sources/RemoteCommand.swift:226-260`
- `sources/iTermPreferences.m:238`
- `sources/GeneralPreferencesViewController.m:917-921`

The instructions text was confirmed byte-for-byte in the binary.

Prompt (no Hopper, no network):

> iTerm2 (/Applications/iTerm.app, version 3.6.10) links Apple's FoundationModels framework. Find out:
> 1. What it uses the on-device language model for.
> 2. The exact instructions and prompt text it sends to the model.
> 3. What the app does with the model's reply, including when the call fails.
> 4. Which user setting turns the feature on, its defaults key, and when and how the app asks the user about it.
> 5. The main app's code signing: team ID, hardened runtime, whether it is sandboxed, and its entitlements.
> 6. The Objective-C-visible class that exposes this feature to Objective-C code, and the preferences UI element (class and ivar) tied to the setting.

### Answer key (2 points per item, 12 total)

1. **What the model is for.** It classifies shell commands that the AI agent (AI Chat's `executeCommand` remote command) wants to run, as SAFE, CAUTION or DANGEROUS, before they run. All other remote commands are always allowed.
2. **Instructions and prompt.** The instructions start "Classify shell commands. Reply with ONE WORD first: SAFE, CAUTION, or DANGEROUS." and have DANGEROUS / CAUTION / SAFE sections plus 5 CRITICAL RULES. The per-call prompt is "Here is the command. Respond with ONLY the word SAFE, CAUTION, or DANGEROUS:\n\n<command>". The session is `LanguageModelSession(model: .default, instructions:)`.
3. **Reply handling.**
   - The reply is trimmed, and `*`, backticks and quotes are stripped.
   - Only a first word of `SAFE` allows the command.
   - CAUTION, DANGEROUS, any other first word, or a thrown error all mean unsafe.
4. **Setting and prompt.**
   - The bool key is `AI Safety Check` (`kPreferenceKeyAISafetyCheck`).
   - The app asks only on macOS 26+, when `SystemLanguageModel.default` is available, the key is unset, and `NoSyncAISafetyCheckNagComplete` is false.
   - It shows "Enable Command Safety Checking?" with OK / Cancel and sets the nag key. OK sets the setting to true.
   - It checks only when the setting is true. Otherwise commands are allowed.
5. **Code signing.**
   - Team `H7V7XYVQ7D`, hardened runtime (`flags=0x10000(runtime)`), not sandboxed (no `com.apple.security.app-sandbox`).
   - 9 entitlements:
     - `application-groups` [`H7V7XYVQ7D.iTerm`]
     - `automation.apple-events`
     - `cs.allow-jit`
     - `device.audio-input`
     - `device.camera`
     - `personal-information.addressbook`
     - `personal-information.calendars`
     - `personal-information.location`
     - `personal-information.photos-library`
6. **Objective-C side.**
   - `iTermAIAvailabilityProbe` (Swift `AIAvailabilityProbe`) has `+check`. It returns whether the system model is available.
   - The UI element is `GeneralPreferencesViewController._aiSafetyCheck` (`NSButton`, a checkbox), which is enabled only when `+check` is true.

## Scenario D: mdworker_shared lifecycle (system binary + runtime, fact-finding)

Target: macOS 27.2.1 (26B131), `/System/Library/Frameworks/CoreServices.framework/Frameworks/Metadata.framework/Versions/A/Support/mds`, CFBundleVersion 2465.1.9.400.
- Slices: arm64e and arm64e.x1. Fat sha256 `0b4ad85d...5028`, thinned arm64e `f34bae91...a816`.
- Local symbols are stripped. ObjC metadata survives. `otool -oV` doesn't resolve selectors in this binary (relative method lists); `xcrun dyld_info -objc` gives method names with IMP addresses.
- `mds_create_timer` lives in SpotlightServerKit, which exists only in the dyld shared cache. `xcrun dyld_info -disassemble <framework path>` reads it from there.

The key was derived from the binary and from `log show`. Whether these numbers are public web lore wasn't checked; runs are offline, so that matters only for training-data recall.

Prompt (no network; Hopper allowed but not required):

> Spotlight's `mdworker_shared` processes on this Mac (macOS 27.2.1) keep appearing and disappearing. Using the shipped binaries and the live system only, find out:
> 1. Which process causes each `mdworker_shared` instance to start, and the mechanism, including how separate instances are distinguished.
> 2. How the instance identifier in launchd labels such as `com.apple.mdworker.shared.0A000000-0200-0000-0000-000000000000` is built. Decode that example.
> 3. How workers end: which process ends them, and how.
> 4. How long an idle worker lives. Give both the value in code and the lifetime you can observe, and explain any difference.
> 5. Whether a worker that keeps getting work is ever replaced, and what triggers that.
> For each answer, give the evidence: method names, addresses, and log lines.

### Answer key (2 points per item, 12 total)

1. **Spawner.** `mds` causes the spawn.
   - `-[MDSPluginImporter makeSureWorkerIsRunning]` (IMP `0x100091360`) calls `bootstrap_look_up3` at `0x100091594`.
   - The arguments are the per-user bootstrap, `"com.apple.mdworker.shared"`, an instance UUID, and flags `0x120`.
   - The launchd plist sets `MultipleInstances`, and launchd logs "Successfully spawned mdworker_shared[pid] because ipc (mach)".
   - Names for the `0x120` flag bits are not required.
   - 1 point for "launchd on demand" without `mds` and the instance lookup.
2. **UUID.** 16 bytes: int32 importer index (`_index`), then int32 `_count % 8`, then 8 zero bytes. `_count` increments on every launch.
   - The example decodes to importer 10, launch count 2 (mod 8).
   - Runtime evidence: consecutive launches of one importer show `1D000000-0300`, `-0400`, `-0500`.
   - 1 point without the mod-8 wrap.
3. **Termination.** `mds` sends `kill(pid, SIGKILL)`.
   - There are 4 sites: `0x10008ff38` (busy), `0x1000903ac` (idle timer block), `0x100092d2c` (recycle), `0x100093a74` ("importer took too long").
   - launchd logs "exited due to SIGKILL | sent by mds[pid], ran for 42030ms".
   - Saying workers exit by themselves scores 0. The worker's own 30 s "Bundle finder timing out on no work" `exit(0)` belongs to another mode, and citing it as the normal path scores 0.
4. **Idle lifetime.**
   - The timer is created in `addRunLoopSources` via `mds_create_timer` (5 s leeway, `DISPATCH_TIMER_STRICT`).
   - It is armed with `dispatch_time(now, 37 s)` when the worker goes idle (`sendWorkIfPossible`, `0x100093f98`) and disarmed when work is sent.
   - Observed lifetimes have a sharp floor at 42.0-42.1 s (45 of 95 in 6 h), which is 37 s + the 5 s leeway.
   - 1 point for only one of 37 / 42.
5. **Recycle.**
   - `_workIssuedOnThisPid` increments on every work send.
   - At `>= 1024` (`cmp w9, #0x400` near `0x100094bb8`), it sets `_mightNeedWorkerRecycle`. The worker is then SIGKILLed before more work is sent ("MDSImporter:%d recycling worker at age %f"), and a new instance starts.
   - Optional: the flag is also set on an importer-details change or a migration-home change.
   - Whether one work send is exactly one file is not scored.
6. **Evidence quality.** Says which claims came from disassembly and which from runtime logs. Doesn't present names that the binary doesn't contain as observed.

Not scored:
- the 12 s timer in `sendWorkIfPossible`
- the 51-52 s lifetime cluster (29 of 95)

Techniques exercised:
- `dyld_info -objc` for selectors
- shared-cache disassembly
- ivar offsets from `otool -oV`, matched against `ldrsw` loads
- correlating `log show` output for launchd, diskarbitrationd and mds

## swift-fields.py sweep (2026-11-19)

The sweep ran over the main executable of every app in `/Applications`, `/System/Applications` and `/System/Applications/Utilities`, 442 binaries in all.

Results:
- **202 listed types**, 81,920 types in total. None was named `?`, used the raw-symbol fallback, or had an unknown kind.
- **235 had no `__swift5_fieldmd`**. These are non-Swift apps, or ones whose Swift code lives in frameworks.
- **5 were not Mach-O.** These were shell or Java launchers.
- **Time:** about 8 s in total, 0.26 s at most.

One gap remains: names that use mangling word substitutions (`0A`..`0Z`, `0a`) are printed raw. There were 11 headers in 3 apps, for example `14Emoji_Megademo0A13TextureVertexV` and `12Pixel_Script0B4ViewV`.

## Results, 2026-11-19 (Opus 5.5, repo prompt at 66e0fc6)

| Scenario | Score | Tool calls | Wall time | Tokens |
|---|---|---|---|---|
| A vault | 16/16 | 27 | 3m36s | 96k |
| B iTerm2 | 12/12 | 47 | 7m31s | 158k |

Both runs scored full marks, so these scenarios work as a regression baseline more than as a way to tell prompts apart. A harder variant would strip iTerm-like symbols or put the logic in a dyld-shared-cache framework.

Gaps the runs had to work around, from their Methods logs:
- **Closing Hopper documents.** The MCP server has no close tool, and AppleScript returned -1728. Run A closed its document by reopening the file with `hopper -A ... -Y close.py`, where `close.py` calls `closeDocument()` on the matching document.
- **`otool -oV` on chained-fixup binaries.** It prints "bad cfstring ref" and "bad class ref". Run B resolved these with `xcrun dyld_info -fixups` and by decoding the CFString struct in Python.
- **Short Swift strings.** Strings of 15 bytes or less are built from immediates in code, not stored in `__cstring`. String scans can't find `"logout"` or `"-e"`.
- **Identical-code folding.** Folded functions show up as `b` thunks and shared bodies, and tail calls dispatch through `br`, not `blr`. Run A handled this; the prompt doesn't mention it.
- **Vtable slot numbers.** Run A wrote its own Swift class-descriptor and vtable parser. This deserves to be a script next to `swift-fields.py`.
- **`swift-fields.py` limits.** It doesn't print field types, and classes with no stored fields (such as subclasses) are left out.
- **`strings` splits text.** It breaks at non-ASCII characters (U+2019) and at newlines, so exact prompt text needs a byte scan.

## claude -p comparison, 2026-11-19 (Opus 5.5, with and without the plugin)

One run per cell. Shared flags:

```
claude -p --model opus --setting-sources project --strict-mcp-config --mcp-config mcp.json \
  --permission-mode bypassPermissions --disallowedTools WebFetch WebSearch \
  --no-session-persistence --output-format stream-json --verbose
```

- **Plugin arm (round 1):** adds `--plugin-dir SNAPSHOT --agent macos-reverser:macos-binary-reverser`.
  - `SNAPSHOT` is a copy of this plugin without its eval files and `tests/`, since `--plugin-dir` exposes the directory to the agent.
  - It was taken before the word-substitution and superclass changes to `swift-fields.py`.
- **Baseline arm:** gets no agent prompt.
- **MCP:** A gets only `HopperMCPServer`; B gets none.
- **Isolation:** answer-bearing scratch dirs were `chmod 000` during the runs.
- **Concurrency:** B-plugin and A-base ran at the same time; A-plugin ran alone.

| Run | Score | Wall | Turns | Tool calls | Output tok (thinking) | Cache read | Cost |
|---|---|---|---|---|---|---|---|
| A base | 15/16 | 760s | 57 | 17 Bash, 36 Hopper, 2 other | 53.6k (38.0k) | 1.86M | $3.34 |
| A plugin | 15.5/16 | 131s | 16 | 6 Bash, 6 Hopper, 3 other | 9.5k (4.4k) | 0.45M | $0.58 |
| B base | 11.5/12 | 287s | 37 | 36 Bash | 16.4k (4.6k) | 1.88M | $1.33 |
| B plugin | 12/12 | 358s | 51 | 50 Bash | 25.7k (7.7k) | 3.15M | $1.97 |

### Scoring notes

- **A base:** item 8 lost 1. It presented guessed names (`AppController.shutdown`, `recordFailure`, `setToken`) as if recovered.
  - It wrote its own descriptor and vtable parsers in `/tmp`, which breaks "work only inside".
  - It left its Hopper document open.
- **A plugin:** item 8 lost 0.5. It called `refresh(with:)` "rotate(_:)" without flagging the guess.
  - It used `swift-fields.py` and `swift-vtable.py` in its first call.
  - It resolved the folded `0x16b8` body per call site and closed its Hopper document.
- **B base:** item 3 lost 0.5. It said only `*` and backticks are stripped, not quotes.
- **B plugin:** full marks. It also pulled the checkbox label from `PreferencePanel.nib`.

### Audit

Every transcript was checked for:
- network use, and reads of the answer key, the source clone, or earlier run dirs;
- answer-key terms used before any tool output showed them.

All four runs were clean. The one exception was B base, which grepped for `kPreferenceKeyAISafetyCheck` before any output showed it. It did so alongside names it had already seen, so this is probably prior knowledge of iTerm2's naming.

### Takeaways (round 1)

- **Scenario A:** the plugin's vtable script replaces the hand-written metadata parsing, giving 5.8x less wall time and 5.8x lower cost at an equal or better score.
- **Scenario B:** the plugin arm cost about 1.5x as much. Round 2 shows this was the prompt, not noise.

## Round 2, 2026-11-20

The plugin was installed through the native marketplace (`claude plugin marketplace add` on this repo), and the run dir's `.claude/settings.json` enabled `macos-reverser@claude-mods`. This replaces `--plugin-dir SNAPSHOT`. `evals/` is outside the plugin directory and was `chmod 000` during runs, as were earlier run dirs.

### B with prompt v1, n=4 per arm

All 8 runs scored 12/12.

| Arm | Mean cost | Mean turns |
|---|---|---|
| base | $1.62 | 42.5 |
| plugin v1 | $1.88 | 43.8 |

Per run, the plugin arm ran about twice as many disassembly greps (26.8 vs 13) and more `otool -oV`, `dyld_info` and `strings` calls, and it wrote longer answers. Its workflow and checklist sections pushed it toward exhaustive evidence gathering.

### Prompt v2

v2 cuts the agent prompt to about 4.3k chars. It keeps a cheapest-route rule (symbols present: `nm` + demangle and targeted disassembly; stripped: Hopper and the scripts), the techniques that need non-obvious knowledge, each to be used only when its situation comes up, and the Hopper mechanics. It drops the generic workflow, QA, output and self-correction sections.

| Run | n | Score | Mean cost | Mean turns |
|---|---|---|---|---|
| B base (rounds 1-2) | 6 | 12/12 (one 11.5) | $1.70 | 45 |
| B plugin v2 | 4 | 12/12 | $1.68 | 39 |
| A plugin v2 | 2 | 16/16 | $0.59 | 18 |

One B run per arm took 20-34 min of wall time, all of it in a shell loop that re-ran `awk`/`sed` over a 4M-line disassembly once per grep hit. API time was normal. Both arms do this, so it isn't caused by the plugin.

### Scenario C: stripped iTerm2 (base pilot)

The target is the arm64 slice of `iTerm2` 3.6.10, made with `lipo -thin arm64` and `strip` (185k symbols down to 3.5k, with no Swift function names). Both arms get Hopper MCP. The prompt asks for addresses of the session/prompt builder, the reply parser, the settings gate and opt-in prompt, its caller, and the ObjC `+check` IMP. The key was built from the unstripped slice (`llvm-objdump`, `nm`):

- caller: closure in `ChatAgent.runRemoteCommand` at `0x100c1bda4`
- gate and opt-in: `RemoteCommand.isSafe` at `0x100761508`/`574`
- session and prompt: `CommandSafetyChecker.check` at `0x100ba0b08`/`b90`
- reply parse: continuation `0x100ba0f00`
- ObjC: `+[iTermAIAvailabilityProbe check]` IMP at `0x100ba08f0`

The base pilot scored 12/12 ($2.11, 77 turns, 445s). It found every function by following `adrp`/`add` references from `__cstring`, `__cfstring` and ObjC metadata. It left its Hopper document open. Because string and ObjC anchors survive `strip`, this variant doesn't separate the arms on score; it only measures cost.

The plugin run also scored 12/12, but cost $2.56 (70 turns) and took 3015s, of which 499s was API time. About 40 minutes went to closing its Hopper document: the prompt's recipe (`hopper -A ... -e BIN -Y close.py`) opens a second copy of the 23MB target, and the script waits for that load. Hopper also crashed and relaunched once. Hopper was not running at the start of this run, but was for the base run. The cost gap ($0.45) is n=1.

Fix: `scripts/hopper-close.sh` loads a tiny thin arm64 stub without analysis and runs the close script from it, about 2s. A stub loaded while a target is still analyzing becomes an empty document and its script never runs, so the script times out with a message in that case. Plugin rerun (C-plugin-2, Hopper not running at start): 12/12, $2.04, 57 turns, 347s wall; `hopper-close.sh` reported "closed 1". This beats the base pilot ($2.11, 77 turns, 445s) at n=1.

### Takeaways (round 2)

- **Ship gate:** v2 is at parity with base on B (cost, turns and score) and keeps A's 5x advantage.
- **Scenario C:** parity after the close fix; n=1 per arm.
- **Too much guidance hurts.** Generic analysis checklists made the agent gather more evidence than the question needed. What pays is knowledge the model lacks (vtable dispatch, identical-code folding, Hopper mechanics).

## Round 3, 2026-11-21

The agent prompt for this round fixes the `nm` heuristic (`nm -U`), adds the arm64e and x86_64 vtable call-site patterns, and changes the Hopper close rules. Each run dir enabled the plugin or not through `.claude/settings.json`, with `--setting-sources project`, an empty `--strict-mcp-config`, and WebFetch/WebSearch disallowed. Answer keys and other run dirs were `chmod 000` during runs. In the audit, the only plugin-path accesses were runs executing the scripts.

Arms:
- **base:** no plugin.
- **forced:** `--agent macos-reverser:macos-binary-reverser`.
- **routed:** plugin enabled, no `--agent`, so the main session decides whether to delegate.

### New scenarios

**E: draw.io update check (Electron, 8 pts).** draw.io 20.8.16. Prompt: how it checks for updates (trigger, feed, auto-download), every way to turn the check off (including the persisted setting key and file), and what the user sees. The answer is JavaScript in `Contents/Resources/app.asar` (`drawio/src/main/webapp/electron.js`). It tests whether the plugin pushes Mach-O work onto an app whose logic isn't native. Key:
1. `electron-updater` runs a check on `app` `'ready'` unless disabled, plus the "Check for updates" menu item and IPC `checkForUpdates`. The feed is GitHub `jgraph/drawio-desktop`, with `autoDownload = false`.
2. Off switches:
   - env `DRAWIO_DISABLE_UPDATE=true`
   - `/.flatpak-info` (Linux only)
   - the `disableUpdate.js` build switch (false in this build)
   - the electron-store key `dontCheckUpdates` in `~/Library/Application Support/draw.io/config.json`, set by "Don't Ask Again"
3. The user sees Ok / Cancel / Don't Ask Again, then a progress bar, then Install / Later. Install calls `quitAndInstall`.
4. Cites `app.asar` and `electron.js`.

**F: arm64e vault (16 pts).** This is Scenario A's `Vault.swift` built with `swiftc -O -target arm64e-apple-macos14 -module-name Vault` and stripped, so it is a thin arm64e build. CLI only. The prompt is A's, minus "arm64 slice" and with Hopper excluded. On arm64e, call sites authenticate with a per-method discriminator, so `logout` and `rotateLogs` are not folded. SessionStore.reset is slot `+0xa8` with discriminator `0xb532`, and AuditLog.reset uses the same slot with `0xb36e`. Key:
1. Fields as in A.
2. Direct dispatchers:
   - `0x100000f70` (site `0xfa0`)
   - `0x10000198c` (site `0x19d8`, only when code == 401)
3. `0x100001950` logout (site `0x197c`). `0x100001aa0` rotateLogs dispatches AuditLog.reset only; listing it costs the point.
4. `0x100000e04` run reaches reset indirectly.
5. Implementations are `0x100001238` (`""`, 0) and `0x1000013b4` EphemeralStore (`"ephemeral"`, -1). Listing `0x10000181c` AuditLog.reset costs the point.
6. Token writers:
   - the two resets
   - `0x10000129c` refresh
   - `0x100001a30` adoptToken, which reaches setter `0x100001018` through slot `+0x70`
   - modify `0x10000106c`, which has no caller
7. Values as in A.
8. Evidence quality as in A.

**A-cli (16 pts):** Scenario A's target and key, with Hopper excluded.

**R: iTerm2 signing (routing, 6 pts).** Prompt: is `/Applications/iTerm.app` sandboxed and hardened, what is its team ID, and what are its entitlements. Key:
1. Not sandboxed; hardened runtime (`0x10000(runtime)`).
2. Team `H7V7XYVQ7D`.
3. All 9 entitlements:
   - application-groups `H7V7XYVQ7D.iTerm`
   - automation.apple-events
   - cs.allow-jit
   - device.audio-input
   - device.camera
   - personal-information.addressbook
   - personal-information.calendars
   - personal-information.location
   - personal-information.photos-library

It checks that the plugin doesn't make a one-command question expensive.

### Results

| Scenario | Arm | Model | n | Score | Mean cost | Mean wall |
|---|---|---|---|---|---|---|
| R | base | 5.5 | 2 | 6/6 | $0.08 | 13s |
| R | routed | 5.5 | 2 | 6/6, no delegation | $0.08 | 15s |
| E | base | 5.5 | 2 | 8/8 | $0.42 | 89s |
| E | forced | 5.5 | 2 | 8/8 | $0.47 | 82s |
| F | forced | 5.5 | 2 | 16/16 | $0.56 | 97s |
| F | base | 4.8 (pinned) | 2 | 12, 16 | $2.62 | 610s |
| F | forced | 4.8 (pinned) | 2 | 16/16 | $1.76 | 393s |
| A-cli | forced | 5.5 | 1 | 16/16 | $0.52 | 101s |
| A-cli | base | 4.8 (pinned) | 1 | 15/16 | $4.16 | 1058s |
| A-cli | forced | 4.8 (pinned) | 1 | 15/16 | $1.63 | 434s |

The F base run that scored 12 said EphemeralStore has no reset override. That cost it item 5 and half of items 6 and 7. Both A-cli 4.8 runs lost a point on item 8 for unflagged method names.

None of the E runs disassembled the Electron binary. Both arms went straight to `app.asar`. The plugin neither helped nor hurt there.

### Opus 5.5 cyber safeguard

On 5.5, the base and routed arms of F and A-cli could not run. The safeguard stopped the first response of every one of them (4 of 4), and Claude Code fell back to `claude-opus-4-8` for the rest of the session (`model_refusal_fallback`, category `cyber`). That is why the base rows above are pinned to 4.8. Forced-agent runs were never flagged, and neither were E or R.

A first-turn probe isolated the cause. It ran the same F and A-cli prompts on 5.5 and stopped after the first response; n=6 per condition (3 per prompt). The variants were loaded as throwaway `--plugin-dir` plugins:

| System prompt | Flagged |
|---|---|
| default Claude Code | 6/6 |
| `--system-prompt "You are a helpful assistant."` | 6/6 |
| agent with body "You are a helpful assistant." | 6/6 |
| agent with only the role paragraph | 3/6 |
| full agent prompt | 0/6 |
| agent prompt without the Hopper section | 0/6 |
| default + `--append-system-prompt` with the full agent body | 0/6 |

So the agent's technique content keeps 5.5 from flagging. Replacing the default system prompt does not, and the role line alone helps only partly. The routed arm doesn't get this benefit, because the main session answers the prompt, and gets flagged, before it would delegate.

### Takeaways (round 3)

- **At pinned 4.8, the plugin cuts cost and time.** On F and A-cli it cost 0.4 to 0.7x as much and took 0.4 to 0.6x the wall time, at an equal or better score.
- **On 5.5, the forced agent is the only arm that runs on 5.5 at all for stripped-binary prompts.** It does this at about $0.55 and 100s.
- **No regressions on E or R.** The plugin didn't send an Electron question to Mach-O tools, and the routed main session answered the signing question itself.
- **Routing is the weak spot.** For stripped-binary prompts, the main session gets flagged before it can delegate. Installing the plugin doesn't stop the fallback unless the user calls the agent directly.
