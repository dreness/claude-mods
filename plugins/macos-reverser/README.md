# macos-reverser

Adds the `macos-reverser:macos-binary-reverser` agent. It reverse-engineers macOS binaries and app bundles with Hopper Disassembler and the Xcode command-line tools (`otool`, `nm`, `lipo`, `codesign`). Use it to learn how an app implements something, pull out strings and resources, check behavior, or look into a suspicious binary.

Claude picks the agent on its own for those tasks, or you can ask for it by name.

## Install

```
/plugin install macos-reverser --marketplace dreness/claude-mods
```

## Requirements

- [Hopper Disassembler](https://www.hopperapp.com) 5 or later. Install its command-line launcher (`/usr/local/bin/hopper`) from Hopper's menu.
- Hopper's MCP server, which the plugin doesn't configure. Add it once:

  ```
  claude mcp add --scope user HopperMCPServer -- /usr/local/bin/HopperMCPServer
  ```

  The server talks to the running Hopper app over XPC. Hopper must be open with the binary loaded.
- Xcode command-line tools (`xcode-select --install`).
- `python3` for `scripts/swift-fields.py`.

## scripts/swift-fields.py

Lists every Swift type in a binary along with its stored fields, marked `let` or `var`, read from `__swift5_fieldmd`. It needs no extra tools and reads thin or universal binaries (the arm64 slice, or the first slice when there is none).

```
$ python3 scripts/swift-fields.py "/Applications/Logic Pro.app/Contents/Frameworks/LogicAppFramework.framework/LogicAppFramework"
class LogicMacAppFactory
  var projects
  var assistantAvailabilityController
...
```

Tests: `python3 -m unittest discover -s plugins/macos-reverser/tests`. They compile small Swift and C dylibs, so they need `swiftc` and `clang`.
