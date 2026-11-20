#!/usr/bin/env python3
"""Tests for scripts/swift-fields.py against freshly compiled Swift binaries.

Run: python3 -m unittest discover -s plugins/macos-reverser/tests
Needs swiftc, clang and lipo (Xcode command line tools).
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "swift-fields.py")
# arm64e app whose field metadata names most of its types through indirect symbolic references.
INDIRECT_SAMPLE = "/System/Applications/Weather.app/Contents/MacOS/Weather"

FIXTURE = """
import CoreGraphics
public final class Sample {
    public let fixed: Int = 1
    public var counter: String = ""
    public init() {}
}
public struct Pair {
    let left: Int
    var right: Double
}
public enum Shape {
    case circle(radius: Double)
    case square
}
public struct Box<T> {
    public var item: T
    public struct Label {
        let text: String
    }
}
public struct Frame {
    public var size: CGSize
}
"""

EXPECTED = """class Sample
  let fixed
  var counter
struct Pair
  let left
  var right
enum Shape
  case circle
  case square
struct Box
  var item
struct Label
  let text
struct Frame
  var size
struct CGSize
  var width
  var height
"""


def load_script():
    spec = importlib.util.spec_from_file_location("swift_fields", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def lipo_create(output, *inputs):
    subprocess.run(["lipo", "-create", *inputs, "-output", output], check=True)
    return output


def run_script(binary):
    return subprocess.run([sys.executable, "-I", SCRIPT, binary], capture_output=True, text=True)


@unittest.skipUnless(all(map(shutil.which, ["swiftc", "clang", "lipo"])), "needs swiftc, clang and lipo")
class SwiftFieldsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workdir = tempfile.mkdtemp(suffix=".noindex")
        cls.arm64 = compile_fixture(cls.workdir, "arm64")
        cls.x86 = compile_fixture(cls.workdir, "x86_64")
        cls.fat = lipo_create(os.path.join(cls.workdir, "libFixture-fat.dylib"), cls.arm64, cls.x86)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.workdir)

    def assert_lists_fixture(self, binary):
        result = run_script(binary)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, EXPECTED)

    def test_thin_binary_lists_fields_cases_and_nested_types(self):
        self.assert_lists_fixture(self.arm64)

    def test_universal_binary_reads_arm64_slice(self):
        self.assert_lists_fixture(self.fat)

    def test_universal_binary_without_arm64_reads_first_slice(self):
        self.assert_lists_fixture(lipo_create(os.path.join(self.workdir, "libFixture-x86fat.dylib"), self.x86))

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

    @unittest.skipUnless(os.path.exists(INDIRECT_SAMPLE), f"needs {INDIRECT_SAMPLE}")
    def test_indirect_symbolic_references_resolve_to_names(self):
        result = run_script(INDIRECT_SAMPLE)
        self.assertEqual(result.returncode, 0, result.stderr)
        headers = [line for line in result.stdout.splitlines() if not line.startswith(" ")]
        self.assertGreater(len(headers), 100)
        self.assertEqual([h for h in headers if h.endswith(" ?")], [])
        self.assertIn("struct CGSize", headers)


class SymbolNameTest(unittest.TestCase):
    def setUp(self):
        self.sf = load_script()

    def test_descriptor_symbols_name_their_type(self):
        self.assertEqual(self.sf.symbol_type_name("_$s10Foundation4DateVMn"), "Date")
        self.assertEqual(self.sf.symbol_type_name("_$sSo7CGPointVMn"), "CGPoint")
        self.assertEqual(self.sf.symbol_type_name("_$sSiMn"), "Int")
        self.assertEqual(self.sf.symbol_type_name("_$s7SwiftUI4ViewP4BodyQzMn"), "_$s7SwiftUI4ViewP4BodyQzMn")

    def test_nested_type_names_the_innermost(self):
        self.assertEqual(self.sf.nominal_name("7Fixture3BoxV5LabelV"), "Label")


if __name__ == "__main__":
    unittest.main()
