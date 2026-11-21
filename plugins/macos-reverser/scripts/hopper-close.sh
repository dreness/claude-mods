#!/bin/bash
# usage: hopper-close.sh BIN...
# Closes the unsaved Hopper documents whose executable path is one of BIN, without
# saving. Documents with a saved database are skipped and reported.
# Hopper can only run a script after loading a document, so this loads a tiny
# thin arm64 stub (no analysis) whose script closes the targets and the stub.
# Run it after the targets' analysis ends: a stub loaded during analysis never runs its script.
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: $0 BIN..." >&2; exit 2; }
dir=$(mktemp -d "${TMPDIR:-/tmp}/hopper-close.XXXXXX.noindex")
trap 'rm -rf "$dir"' EXIT
echo 'int main(void){return 0;}' | clang -arch arm64 -x c - -o "$dir/stub"
python3 - "$dir" "$@" <<'PY'
import json, os, sys
d = sys.argv[1]
paths = [os.path.realpath(p) for p in sys.argv[2:]] + [os.path.realpath(d + "/stub")]
open(d + "/close.py", "w").write(f"""import os
paths = {json.dumps(paths)}
n = 0
kept = []
for doc in Document.getAllDocuments():
    p = doc.getExecutableFilePath()
    if p and os.path.realpath(p) in paths:
        if doc.getDatabaseFilePath():
            kept.append(doc.getDatabaseFilePath())
            continue
        doc.closeDocument()
        n += 1
msg = "closed %d\\n" % (n - 1)
for k in kept:
    msg += "kept saved document %s\\n" % k
open({json.dumps(d + "/log")}, "w").write(msg)
""")
PY
hopper -A -l Mach-O -e "$dir/stub" -Y "$dir/close.py" >/dev/null
for _ in $(seq 60); do
  [ -f "$dir/log" ] && { cat "$dir/log"; exit 0; }
  /bin/sleep 0.5
done
echo "timed out: is Hopper still analyzing? Retry when analysis ends." >&2
exit 1
