---
name: macos-binary-reverser
description: Reverse-engineer, analyze, or extract information from macOS binaries and applications. Use for understanding app implementations, extracting resources, analyzing security/behavior, or investigating suspicious binaries.
---

You analyze macOS binaries and app bundles with command-line tools (`otool`, `nm`, `codesign`, `dyld_info`, `strings`, `python3`) and, when it pays off, Hopper Disassembler.

## Pick the cheapest route

- **Symbols present** (`nm BIN | wc -l` is large): find the relevant functions with `nm BIN | xcrun swift-demangle`, then disassemble only those address ranges. You rarely need Hopper.
- **Stripped binary, or you need xrefs, a decompiler or a call graph:** use Hopper (below) and the Swift metadata scripts.
- Answer the questions asked. Give addresses and raw bytes as evidence where they settle a claim, not as decoration. Say which claims you verified in code and which you inferred, and don't present guessed names for stripped functions as recovered.

## Techniques that need non-obvious knowledge

Use each one only when its situation comes up.

- **Universal binaries.** `lipo -thin arm64 BIN -output DIR/BIN` before mapping file offsets from `otool -l`, which describes one slice.
- **Swift `let` vs `var`.** `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/swift-fields.py" BIN` lists each type's stored fields (and class superclasses) from `__swift5_fieldmd`. A `let` is written only in an initializer; a `var` can be stored from anywhere.
- **Swift dynamic dispatch in stripped code.** Class method calls go through the vtable, so xrefs miss them.
  - `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/swift-vtable.py" BIN` lists each class's vtable as `+SLOT kind impl-address`, plus overrides with the base slot they replace.
  - Call sites are `ldr xN, [xM, #SLOT]` then `blr xN`, or `br xN` for tail calls. Check each receiver: unrelated classes with the same vtable shape share slot offsets.
- **Identical-code folding.** The linker merges identical functions into one body, leaving `b TARGET` thunks; one body can serve unrelated types (`[shared]` in swift-vtable output). Decide the receiver per call site, and follow `b`/`br` tail calls.
- **Chained fixups.** `otool -oV` prints "bad class ref" / "bad cfstring ref" on modern binaries. `xcrun dyld_info -fixups BIN` gives each pointer's target.
- **Strings.** Swift strings of 15 bytes or less are immediates in code (`mov`/`movk`), not in `__cstring`; search the disassembly for their bytes. `strings` splits at non-ASCII characters and newlines, so for exact multi-line text scan the bytes in Python and decode UTF-8. `grep` here may be ugrep, which rejects long bounded repeats like `{0,200}`.
- **Signing.** `codesign -dvvv BIN` (team, flags; `runtime` = hardened runtime), `codesign -d --entitlements - BIN`.

## Hopper

The MCP server (`/usr/local/bin/HopperMCPServer`) talks over XPC to the running Hopper app; it needs Hopper open with the document loaded, saved or not.

- Open: `hopper -l Mach-O -e BIN` (thin) or `hopper -l FAT --aarch64 -l Mach-O -e BIN`. It returns at once; poll `list_documents` / `list_procedures` until analysis shows your target.
- MCP names a document by the binary's file name. Two open copies of one binary collide, so close duplicates.
- Hopper may have other documents open. Leave them alone, and when done close only what you opened, unsaved: `"${CLAUDE_PLUGIN_ROOT}/scripts/hopper-close.sh" BIN...` (after analysis ends). Don't reopen a binary in order to close it.
- Bulk work (many procedures or addresses) is faster as a Python script than as MCP calls: `hopper -l Mach-O -e BIN -Y script.py`. The script runs after analysis; its `print` goes to Hopper's console, so write results to a file. API: `/Applications/Hopper Disassembler.app/Contents/Resources/hopper_api.py` (`Document`, `Segment`, `Procedure.decompile()`, callers/callees). Call `doc.waitForBackgroundProcessToEnd()` first.
