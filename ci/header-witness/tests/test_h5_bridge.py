"""Tests for ci/header-witness/h5_bridge.py: the comparison-side plumbing, with a stand-in compiler that serves the
VC7.1 objects recorded in fixtures/evidence-vc71.json (keyed by SHA-256 of header bytes, "|", source)."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import unittest.mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import h5_bridge as bridge  # noqa: E402
import h5_witness as witness  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fixtures/evidence-vc71.json").read_text())
H, TU = "include/Foo.h", "src/t.cpp"
BASE, SOURCE = FIXTURE["base"].encode(), FIXTURE["source"].encode()


def recorded(header, source):
    return bytes.fromhex(FIXTURE["objects"][hashlib.sha256(header + b"|" + source).hexdigest()])


OTHER = "src/other.cpp"
OTHER_SOURCE = b"int other;\n"


class FakeGit:
    def __init__(self, head):
        self.files = {"base": {H: BASE, TU: SOURCE, OTHER: OTHER_SOURCE},
                      "tip": {H: head, TU: SOURCE, OTHER: OTHER_SOURCE}}

    def run(self, *args, check=True):
        if args[0] == "show":
            rev, path = args[1].split(":", 1)
            data = self.files[rev].get(path)
            return subprocess.CompletedProcess(args, 0 if data is not None else 128, data or b"", b"")
        if args[0] == "rev-parse":
            return subprocess.CompletedProcess(args, 0, (("1" if args[1].startswith("base") else "2") * 40).encode(), b"")
        raise AssertionError(args)


def worker(git, calls):
    encode = lambda data: base64.b64encode(data).decode()          # noqa: E731
    decode = lambda text, limit: base64.b64decode(text)            # noqa: E731

    def projection(_git, rev):
        files = git.files["base" if rev == "base" else "tip"]
        return ({path: encode(data) for path, data in files.items()},
                {path: hashlib.sha256(data).hexdigest() for path, data in files.items()})

    def request(operation, wire):
        doc = json.loads(wire)
        sources = doc["source"]
        for row in doc["plan"]:
            unit = decode(sources[row["tu"]], 0)
            if row["source"] != hashlib.sha256(unit).hexdigest():
                raise AssertionError("the compile row does not name the TU bytes sent")
        kind = "probe" if decode(sources[TU], 0) != SOURCE else "tu"
        calls.append((kind, tuple(row["tu"] for row in doc["plan"])))
        results = []
        for row in doc["plan"]:
            unit, header = decode(sources[row["tu"]], 0), decode(sources[H], 0)
            if b'#include "Foo.h"' not in unit:
                # Like the real compiler: a unit that never includes Foo.h cannot compile a probe of Foo.
                results.append({"tu": row["tu"], "status": "error", "diagnostics": "C2065"})
                continue
            key = unit
            if hashlib.sha256(header + b"|" + key).hexdigest() not in FIXTURE["objects"]:
                head_part, _, rest = unit.partition(b"\n#include <stddef.h>")
                key = head_part + b'\n#include "Foo.h"\n#include <stddef.h>' + rest   # probe sent without its include
            results.append({"tu": row["tu"], "status": "compiled", "artifact": encode(recorded(header, key)),
                            "includes": [["source", H, hashlib.sha256(header).hexdigest()]]})
        return {"results": results}

    return SimpleNamespace(projection=projection, encode=encode, decode=decode, pack_sources=lambda files: files,
                           MAX_OBJECT=1 << 24, MAX_SOURCE=1 << 20, profile="p" * 64, roots={}), request


def measurement(head_obj, credited):
    _, symbols, _ = witness._coff(head_obj)
    def symbol(name):
        return next(item[1] for item in symbols if item[1].startswith(f"?{name}@@") and item[3] > 0)
    old = {TU: {"rows": [{"rva": hex(0x1000 + i), "status": "NONMATCH", "symbol": symbol(n)}
                         for i, n in enumerate(("useA", "useB"))]}}
    new = {TU: {"rows": [{"rva": hex(0x1000 + i), "symbol": symbol(n),
                          "status": "MATCH" if n in credited else "NONMATCH",
                          "proof": "proven" if n in credited else None}
                         for i, n in enumerate(("useA", "useB"))]}}
    return old, new


def run(case, includes=True, receipts=True, paths=(TU,)):
    head = FIXTURE["cases"][case]["head"].encode()
    head_obj = recorded(head, SOURCE)
    git, calls = FakeGit(head), []
    fake, request = worker(git, calls)
    old, new = measurement(head_obj, FIXTURE["cases"][case]["credited"])
    with tempfile.TemporaryDirectory() as tmp:
        for side in ("base", "head"):
            (Path(tmp) / side / "obj").mkdir(parents=True)
            if receipts:
                (Path(tmp) / side / "obj" / "src_t.cpp.obj.includes.json").write_text(
                    json.dumps(["/job/" + H] if includes else ["/job/include/Other.h"]))
        (Path(tmp) / "head/obj" / "src_t.cpp.obj").write_bytes(head_obj)
        text = {TU: SOURCE, OTHER: OTHER_SOURCE}
        rows = [{"tu": tu, "source": hashlib.sha256(text[tu]).hexdigest(), "contract": "c", "flags": [], "env": {}}
                for tu in paths]
        native = {"profile_sha256": "p" * 64, "compile": {"head": rows, "base": rows}}
        plan = {"changed_paths": [H, TU], "base": "base", "request_paths": list(paths)}
        binding = {"repo": 1, "number": 9, "base": "b" * 40, "head": "h" * 40, "tree": "t" * 40,
                   "issuer": {"run": "5", "attempt": "1"}}
        result = bridge.narrow(plan, native, old, new, tmp, git, "tip", request, fake, Path(tmp) / "out", binding)
        written = json.loads((Path(tmp) / "out/header-h5.json").read_text())
    return result, written, calls


class Bridge(unittest.TestCase):
    def test_compatible_split_needs_no_tu_compile(self):
        result, written, calls = run("split (compatible)")
        self.assertEqual(written, result)
        self.assertEqual(written["binding"]["issuer"], {"run": "5", "attempt": "1"})
        self.assertTrue(result["headers"][H]["layout"]["compatible_shape"])
        self.assertEqual([c for c in calls if c[0] == "tu"], [])
        self.assertEqual([c[1] for c in calls if c[0] == "probe"], [(TU,), (TU,)])   # base and head

    def test_swap_with_both_functions_newly_matched_gets_two_witnesses(self):
        result, _, calls = run("swap a/b, both used")
        reads = result["headers"][H]["access"]["reads"]
        self.assertEqual(sorted((r["identity"], r["rva"]) for r in reads), [("a", 0x1000), ("b", 0x1001)])
        self.assertEqual({c[1] for c in calls if c[0] == "tu"}, {(TU,)})

    def test_a_probe_needs_a_tu_that_really_includes_the_header(self):
        result, _, calls = run("split (compatible)", includes=False)
        evidence = result["headers"][H]
        self.assertFalse(evidence["layout"]["complete"])
        self.assertIn("No translation unit", evidence["held"][0])
        self.assertEqual([c for c in calls if c[0] == "probe"], [])

    def test_without_receipts_the_host_must_include_the_header_itself(self):
        result, _, calls = run("split (compatible)", receipts=False)
        self.assertTrue(result["headers"][H]["layout"]["compatible_shape"])
        self.assertEqual(len([c for c in calls if c[0] == "probe"]), 2)
        result, _, _ = run("split (compatible)", receipts=False, paths=(OTHER,))
        self.assertFalse(result["headers"][H]["layout"]["complete"])
        self.assertIn("No translation unit", result["headers"][H]["held"][0])

    def test_only_newly_credited_rows_witness(self):
        result, _, _ = run("swap a/b, only a used")
        self.assertEqual([r["identity"] for r in result["headers"][H]["access"]["reads"]], ["a"])

    def test_credited_rows_need_match_proof_and_a_change(self):
        old = {TU: {"rows": [{"rva": "0x10", "status": "MATCH", "proof": "proven", "symbol": "?f@@YAXXZ"}]}}
        new = {TU: {"rows": [{"rva": "0x10", "status": "MATCH", "proof": "proven", "symbol": "?f@@YAXXZ"},
                             {"rva": "0x20", "status": "MATCH", "proof": None, "symbol": "?g@@YAXXZ"},
                             {"rva": "0x30", "status": "MATCH", "proof": "proven", "symbol": "?h@@YAXXZ"}]}}
        self.assertEqual([row["rva"] for row in bridge.credited_rows(old, new)], [0x30])



    def test_includer_compile_failure_reports_probe_compile_error_and_diagnostic(self):
        head_hdr = FIXTURE["cases"]["split (compatible)"]["head"].encode()
        head_obj = recorded(head_hdr, SOURCE)
        plan = {"changed_paths": [H], "request_paths": [TU], "base": "base", "number": 9, "repo": 1}
        native = {"profile_sha256": "p" * 64, "compile": {
            side: [{"tu": TU, "source": "s", "contract": "c", "flags": [], "env": {}}] for side in ("base", "head")}}
        with tempfile.TemporaryDirectory() as tmp:
            for side in ("base", "head"):
                (Path(tmp) / side / "obj").mkdir(parents=True)
                (Path(tmp) / side / "obj" / "src_t.cpp.obj.includes.json").write_text(json.dumps(["/job/" + H]))
            (Path(tmp) / "head/obj" / "src_t.cpp.obj").write_bytes(head_obj)
            git = FakeGit(head_hdr)
            w = SimpleNamespace(
                projection=lambda g, r: ({TU: base64.b64encode(SOURCE).decode(), H: base64.b64encode(BASE if r == "base" else head_hdr).decode()}, {}),
                encode=lambda b: base64.b64encode(b).decode(),
                decode=lambda s, lim: base64.b64decode(s),
                pack_sources=lambda d: d, MAX_OBJECT=10**7, MAX_SOURCE=10**7,
            )
            req = lambda op, wire: {"results": [{"tu": TU, "status": "error", "diagnostics": "error C2259: abstract\nmore"}]}
            out = bridge.narrow(plan, native, {}, {}, tmp, git, "tip", req, w, Path(tmp) / "out", {"repo": 1})
        held = out["headers"][H]["held"]
        self.assertEqual(len(held), 1)
        self.assertIn("failed to compile on the base side", held[0])
        self.assertIn("error C2259: abstract", held[0])
        self.assertNotIn("No translation unit", held[0])


class FromCompare(unittest.TestCase):
    """The comparison-side entry: built from the comparison's own modules, bound to the run attempt, size-bounded."""

    def stubs(self, captured):
        def narrow(plan, native, old, new, work, git, tip, request, worker, out_root, binding):
            captured.update(request=request, worker=worker, binding=binding)
            Path(out_root).mkdir(exist_ok=True)
            Path(out_root, "header-h5.json").write_text("x" * captured.get("size", 10))
        modules = {
            "header_worker_proof": SimpleNamespace(plan="PLAN", collect="COLLECT"),
            "measure": SimpleNamespace(COMPILER="c" * 64, request="REQUEST"),
            "pool_protocol": SimpleNamespace(encode="ENC", decode="DEC", pack_sources="PACK", MAX_OBJECT=7,
                                             MAX_FILE=8),
            "common": SimpleNamespace(PIN={"profile_sha256": "p" * 64}),
            "pool_compare": SimpleNamespace(projection="PROJECTION"),
        }
        return narrow, modules

    def call(self, size=10):
        captured = {"size": size}
        narrow, modules = self.stubs(captured)
        plan = {"repo": 1, "number": 9, "base": "b" * 40, "head": "h" * 40, "tree": "t" * 40}
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.dict(sys.modules, modules), unittest.mock.patch.object(bridge, "narrow", narrow):
            written = bridge.from_compare(plan, {}, {}, {}, tmp, None, "tip", {"r": 1}, Path(tmp) / "h5",
                                          env={"GITHUB_RUN_ID": "77", "GITHUB_RUN_ATTEMPT": "2"})
            exists = (Path(tmp) / "h5/header-h5.json").exists()
        return written, exists, captured

    def test_the_worker_and_binding_come_from_the_comparison(self):
        written, exists, captured = self.call()
        self.assertTrue(written and exists)
        worker = captured["worker"]
        self.assertEqual((worker.projection, worker.encode, worker.profile, worker.roots),
                         ("PROJECTION", "ENC", "p" * 64, {"r": 1}))
        self.assertEqual(captured["request"], "REQUEST")
        self.assertEqual(captured["binding"], {"repo": 1, "number": 9, "base": "b" * 40, "head": "h" * 40,
                                               "tree": "t" * 40, "issuer": {"run": "77", "attempt": "2"}})

    def test_an_oversized_receipt_is_removed(self):
        written, exists, _ = self.call(size=bridge.MAX_RECEIPT + 1)
        self.assertEqual((written, exists), (False, False))


if __name__ == "__main__":
    unittest.main()
