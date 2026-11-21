# macos-reverser

Adds the `macos-reverser:macos-binary-reverser` agent. It reverse-engineers macOS binaries and app bundles with Hopper Disassembler and the Xcode command-line tools (`otool`, `nm`, `lipo`, `codesign`). Use it to learn how an app implements something, pull out strings and resources, check behavior, or look into a suspicious binary.

Claude picks the agent on its own for those tasks, or you can ask for it by name. For a session spent mostly on reverse engineering, run the agent as the main session:

```
claude --agent macos-reverser:macos-binary-reverser
```

In our evals this cost 0.4 to 0.7x as much and took about half the time of a session without the plugin, with equal or better answers. Curiously, running this way also seems to result in fewer refusals...

## Install

```
/plugin marketplace add dreness/claude-mods
/plugin install macos-reverser@claude-mods
```

## Model fallback

On Opus 5.5, Claude Code's cyber safeguard can flag reverse-engineering requests, particularly ones about stripped binaries. When it does, the session switches to an earlier Opus model for the rest of the session. This is expected behavior, not a plugin error, and the work still gets done.

## Requirements

- [Hopper Disassembler](https://www.hopperapp.com) 5 or later. Install its command-line launcher (`/usr/local/bin/hopper`) from Hopper's menu.
- Hopper's MCP server, which the plugin doesn't configure. Add it once:

  ```
  claude mcp add --scope user HopperMCPServer -- /usr/local/bin/HopperMCPServer
  ```

  The server talks to the running Hopper app over XPC. Hopper must be open with the binary loaded.
- Xcode command-line tools (`xcode-select --install`).
- `python3` for `scripts/swift-fields.py` and `scripts/swift-vtable.py`.

## scripts/swift-fields.py

Lists every Swift type in a binary along with its stored fields, marked `let` or `var`, read from `__swift5_fieldmd`. It needs no extra tools and reads thin or universal binaries (the arm64 slice, or the first slice when there is none).

```
$ python3 scripts/swift-fields.py "/Applications/Logic Pro.app/Contents/Frameworks/LogicAppFramework.framework/LogicAppFramework"
class LogicMacAppFactory
  var projects
  var assistantAvailabilityController
...
```

## scripts/swift-vtable.py

Lists each Swift class's vtable from `__swift5_types`: the slot's byte offset in the class metadata (the `#SLOT` in `ldr xN, [xM, #SLOT]` before a `blr`), its kind and its implementation address, plus override entries with the base-class slot they replace. Works on stripped binaries; names implementations when symbols are present.

```
$ python3 scripts/swift-vtable.py vault
class SessionStore  (descriptor 0x100001c20)
  +0x68  getter         0x100000eb8
...
  +0xa8  method         0x1000010b0
class EphemeralStore : SessionStore  (descriptor 0x100001ca4)
  override +0xa8 SessionStore method         0x1000011a0
```

## scripts/hopper-close.sh

`hopper-close.sh BIN...` closes the Hopper documents loaded from those files, without saving. It skips documents that have a saved database. Neither the MCP server nor AppleScript can close documents. The script compiles a tiny arm64 stub with `clang` and loads it in Hopper without analysis, and the stub's Python script closes the targets and itself. It takes about 2s. Run it after analysis ends: a stub loaded during analysis never runs its script.

Tests: `python3 -m unittest discover -s plugins/macos-reverser/tests`. They compile small Swift and C dylibs, so they need `swiftc` and `clang`.
