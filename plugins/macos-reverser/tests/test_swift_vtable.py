#!/usr/bin/env python3
"""Tests for scripts/swift-vtable.py against the scenario A fixture and a generic/async fixture.

Run: python3 -m unittest discover -s plugins/macos-reverser/tests
Needs swiftc, strip and nm (Xcode command line tools).
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "swift-vtable.py")
VAULT = os.path.join(HERE, "..", "..", "..", "evals", "macos-reverser", "vault", "Vault.swift")

EXTRA = """
open class Box<T> {
    public var item: T
    public init(_ item: T) { self.item = item }
    open func fetch() async -> Int { 1 }
    open func name() -> String { "box" }
}
public final class IntBox: Box<Int> {
    public override func fetch() async -> Int { 2 }
}
public func use(_ b: Box<Int>) async -> String { _ = await b.fetch(); return b.name() }
"""


def compile_swift(workdir, src, name, *flags):
    out = os.path.join(workdir, name)
    subprocess.run(["swiftc", *flags, "-target", "arm64-apple-macos14", "-o", out, src],
                   check=True, cwd=workdir, capture_output=True)
    return out


def run_script(binary):
    return subprocess.run([sys.executable, "-I", SCRIPT, binary], capture_output=True, text=True)


def symbol_addresses(binary):
    """Demangled simplified name: address, from nm."""
    nm = subprocess.run(["nm", "-n", binary], check=True, capture_output=True, text=True).stdout
    pairs = [line.split() for line in nm.splitlines()]
    pairs = [(int(p[0], 16), p[2]) for p in pairs if len(p) == 3]
    names = subprocess.run(["xcrun", "swift-demangle", "--simplified", "--compact"],
                           input="\n".join(n for _, n in pairs), check=True, capture_output=True, text=True).stdout
    return {name: addr for (addr, _), name in zip(pairs, names.splitlines())}


def entries(output):
    """{(class, slot): address} for vtable and resolved override lines."""
    out, cls = {}, None
    for line in output.splitlines():
        if line.startswith("class "):
            cls = line.split()[1]
            continue
        m = re.match(r"  (?:override )?\+(0x[0-9a-f]+) .*?(0x[0-9a-f]+)", line)
        if m:
            out[(cls, int(m.group(1), 16))] = int(m.group(2), 16)
    return out


@unittest.skipUnless(all(map(shutil.which, ["swiftc", "strip", "nm", "xcrun"])), "needs swiftc, strip, nm and xcrun")
class SwiftVTableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workdir = tempfile.mkdtemp(suffix=".noindex")
        cls.vault = compile_swift(cls.workdir, VAULT, "vault", "-O", "-module-name", "Vault")
        cls.stripped = os.path.join(cls.workdir, "vault-stripped")
        shutil.copy(cls.vault, cls.stripped)
        subprocess.run(["strip", cls.stripped], check=True)
        src = os.path.join(cls.workdir, "Extra.swift")
        with open(src, "w") as f:
            f.write(EXTRA)
        cls.extra = compile_swift(cls.workdir, src, "libExtra.dylib", "-emit-library", "-module-name", "Extra")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.workdir)

    def listing(self, binary):
        result = run_script(binary)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.returncode, 0)
        return result.stdout

    def test_slots_point_at_the_named_implementations(self):
        syms = symbol_addresses(self.vault)
        got = entries(self.listing(self.vault))
        expected = {
            ("SessionStore", 0x70): "SessionStore.token.setter",
            ("SessionStore", 0xa8): "SessionStore.reset()",
            ("SessionStore", 0xb0): "SessionStore.refresh(with:)",
            ("EphemeralStore", 0xa8): "EphemeralStore.reset()",
            ("AuditLog", 0xa8): "AuditLog.reset()",
        }
        for key, name in expected.items():
            self.assertEqual(got.get(key), syms[name], f"{key} should be {name}")

    def test_stripped_binary_gives_the_same_addresses(self):
        self.assertEqual(entries(self.listing(self.stripped)), entries(self.listing(self.vault)))

    def test_override_names_the_base_class_slot(self):
        self.assertRegex(self.listing(self.stripped), r"class EphemeralStore : SessionStore.*\n(  .*\n)*  override \+0xa8 SessionStore method ")

    def test_generic_class_and_async_method_resolve_to_code(self):
        syms = symbol_addresses(self.extra)
        out = self.listing(self.extra)
        got = entries(out)
        box_slots = {slot: addr for (cls, slot), addr in got.items() if cls == "Box"}
        self.assertIn(syms["Box.name()"], box_slots.values())
        fetch = syms["Box.fetch()"]
        self.assertIn(fetch, box_slots.values())
        slot = next(s for s, a in box_slots.items() if a == fetch)
        self.assertEqual(got[("IntBox", slot)], syms["IntBox.fetch()"])
        self.assertIn("method async", out)


if __name__ == "__main__":
    unittest.main()
