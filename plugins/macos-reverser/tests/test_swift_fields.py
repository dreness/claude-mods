#!/usr/bin/env python3
# ABOUTME: Tests swift-fields.py against Swift and C dylibs compiled at test time.
# ABOUTME: Covers thin arm64, universal, and non-Swift inputs.
"""Tests for scripts/swift-fields.py against freshly compiled Swift binaries.

Run: python3 -m unittest discover -s plugins/macos-reverser/tests
Needs swiftc, clang and lipo (Xcode command line tools).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "swift-fields.py")

FIXTURE = """
public final class Sample {
    public let fixed: Int = 1
    public var counter: String = ""
    public init() {}
}
public struct Pair {
    let left: Int
    var right: Double
}
"""

EXPECTED = """class Sample
  let fixed
  var counter
struct Pair
  let left
  var right
"""


def compile_fixture(workdir, arch):
    src = os.path.join(workdir, "Fixture.swift")
    with open(src, "w") as f:
        f.write(FIXTURE)
    out = os.path.join(workdir, f"libFixture-{arch}.dylib")
    subprocess.run(
        ["swiftc", "-emit-library", "-target", f"{arch}-apple-macos13", "-module-name", "Fixture", "-o", out, src],
        check=True, cwd=workdir, capture_output=True,
    )
    return out


def run_script(binary):
    return subprocess.run([sys.executable, "-I", SCRIPT, binary], capture_output=True, text=True)


@unittest.skipUnless(all(map(shutil.which, ["swiftc", "clang", "lipo"])), "needs swiftc, clang and lipo")
class SwiftFieldsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workdir = tempfile.mkdtemp(suffix=".noindex")
        cls.arm64 = compile_fixture(cls.workdir, "arm64")
        x86 = compile_fixture(cls.workdir, "x86_64")
        cls.fat = os.path.join(cls.workdir, "libFixture-fat.dylib")
        subprocess.run(["lipo", "-create", cls.arm64, x86, "-output", cls.fat], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.workdir)

    def test_thin_binary_lists_let_and_var_fields(self):
        result = run_script(self.arm64)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, EXPECTED)

    def test_universal_binary_reads_arm64_slice(self):
        result = run_script(self.fat)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, EXPECTED)

    def test_binary_without_swift_metadata_is_an_error(self):
        src = os.path.join(self.workdir, "plain.c")
        with open(src, "w") as f:
            f.write("int plain(void) { return 1; }\n")
        lib = os.path.join(self.workdir, "libplain.dylib")
        subprocess.run(["clang", "-dynamiclib", "-o", lib, src], check=True, capture_output=True)
        result = run_script(lib)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("no __swift5_fieldmd section", result.stderr)


if __name__ == "__main__":
    unittest.main()
