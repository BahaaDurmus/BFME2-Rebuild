"""Run H5 inside the narrow comparison (ci/managed-check/compare.py), where the authenticated head objects, the head
compile rows and the compiler request are in scope. Called like the inline export, behind BFME_H5_EXPORT:

    from h5_bridge import narrow
    narrow(plan, native, old, new, work, git, tip, request, worker, out_root, binding)

  plan     the admitted plan (changed_paths, request_paths, base, ...)
  native   plan plus compile={"base": rows, "head": rows} (each row: tu, source, contract, flags, env)
  old/new  measure results per TU: {"rows": [{"rva": "0x...", "status", "credited", "proof", "symbol", ...}], ...}
  work     the comparison's private directory: work/head/obj/<tu with / as _>.obj
  git, tip the protected repository and the reconstructed head commit
  request  the sealed compiler request function (measure.request)
  worker   a namespace of the pool helpers (projection, encode, decode, pack_sources, MAX_OBJECT, MAX_SOURCE),
           profile and roots; passed in so tests can replace the compiler

Layout probes are appended to an includer TU and compiled with that TU's own compile row, so they see exactly the
compile context of the comparison; the witness compiles use the head rows with only the header bytes replaced.

  binding  {"repo", "number", "base", "head", "tree", "issuer": {"run", "attempt"}} of this protected attempt

Writes out_root/header-h5.json: {"version": 1, "binding", "headers": {path: evidence}, "held"}; the caller wraps it
so that a failure here never changes the comparison.
"""
import hashlib
import json
from pathlib import Path

import h5_evidence as h5
from h5_witness import Held

MAX_HEADERS = 4


def credited_rows(old, new):
    """Rows newly credited with proof by this change, per TU, in the form evidence() takes."""
    out = []
    for path, item in sorted(new.items()):
        before = {row["rva"]: row for row in (old.get(path) or {}).get("rows", [])}
        for row in item.get("rows", []):
            prior = before.get(row["rva"], {})
            now_credited = row.get("credited", row.get("status") == "MATCH") and row.get("proof") == "proven"
            was_credited = prior.get("credited", prior.get("status") == "MATCH") and prior.get("proof") == "proven"
            if now_credited and row.get("status") == "MATCH" and not was_credited and row.get("symbol"):
                out.append({"rva": int(row["rva"], 16), "path": path, "symbol": row["symbol"],
                            "before": {"credited": False},
                            "after": {"credited": True, "proof": True, "status": "MATCH"}})
    return out


def narrow(plan, native, old, new, work, git, tip, request, worker, out_root, binding):
    headers = [path for path in plan["changed_paths"] if path.startswith("include/")]
    result = {"version": 1, "binding": binding, "headers": {}, "held": []}
    if len(headers) > MAX_HEADERS:
        result["held"].append(f"{len(headers)} headers changed; H5 measures at most {MAX_HEADERS}.")
        headers = []
    rows = credited_rows(old, new)
    head_objects = {}
    for path in sorted({row["path"] for row in rows}):
        obj = Path(work) / "head" / "obj" / (path.replace("/", "_") + ".obj")
        if obj.is_file():
            head_objects[path] = obj.read_bytes()
    for row in rows:
        if row["path"] in head_objects:
            row["head_object"] = hashlib.sha256(head_objects[row["path"]]).hexdigest()
    rows = [row for row in rows if "head_object" in row]
    projections = {"base": worker.projection(git, plan["base"]), "head": worker.projection(git, tip)}
    head_rows = {row["tu"]: row for row in native["compile"]["head"]}

    def show(rev, path):
        done = git.run("show", f"{rev}:{path}", check=False)
        return done.stdout if done.returncode == 0 else None

    def includer_of(side, header, rows):
        """The first planned TU whose own compile on that side read this header (its .includes.json receipt)."""
        for path in plan["request_paths"]:
            listing = Path(work) / side / "obj" / (path.replace("/", "_") + ".obj.includes.json")
            if path not in rows or not listing.is_file():
                continue
            try:
                names = json.loads(listing.read_text())
            except ValueError:
                continue
            if any(str(name).replace("\\", "/").endswith("/" + header) for name in names):
                return path
        return None

    def probe_compile(side, header_bytes, probe_source, header):
        """Append the probe to an includer TU and compile it with that TU's own compile row on that side; a probe
        compiled in any other context could see another layout (a different STL, flags or SDK headers)."""
        rows = {row["tu"]: row for row in native["compile"][side]}
        includer = includer_of(side, header, rows)
        # Without a compile receipt naming an includer (the wide route), try a few planned TUs and accept one only
        # when the probe compile's own include list shows it read this header.
        receipts = any((Path(work) / side / "obj" / (path.replace("/", "_") + ".obj.includes.json")).is_file()
                       for path in plan["request_paths"])
        if includer is None and receipts:
            raise Held(f"No translation unit on the {side} side compiles {header}; its layout cannot be measured.")
        candidates = [includer] if includer else [path for path in plan["request_paths"] if path in rows][:4]
        appended = probe_source
        if includer is None and appended.startswith(b'#include "'):
            # The probe must not pull the header in itself: only a unit that already includes it may host the probe.
            appended = appended.split(b"\n", 1)[1]
        for candidate in candidates:
            sources = dict(projections[side][0])
            unit = worker.decode(sources[candidate], worker.MAX_SOURCE) + b"\n" + appended
            sources[header] = worker.encode(header_bytes)
            sources[candidate] = worker.encode(unit)
            row = dict(rows[candidate], source=hashlib.sha256(unit).hexdigest())
            wire = json.dumps({"version": 1, "profile": native["profile_sha256"],
                               "source": worker.pack_sources(sources), "plan": [row]},
                              sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            reply = request("compile", wire)
            found = (reply.get("results") or [{}])[0]
            if found.get("tu") != candidate:
                if includer is not None:
                    raise Held(f"Layout probe for {header} returned an unexpected compiler result on the {side} side.")
                continue
            if found.get("status") != "compiled":
                if includer is not None:
                    diag = ""
                    raw_diag = found.get("diagnostics") or ""
                    if isinstance(raw_diag, str):
                        first = next((ln.strip() for ln in raw_diag.splitlines() if ln.strip()), "")
                        clean = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in first)[:100].strip()
                        if clean:
                            diag = f" ({clean})"
                    raise Held(f"Layout probe for {header} failed to compile on the {side} side{diag}.")
                continue
            read = {tuple(item[:3]) for item in found.get("includes", []) if isinstance(item, (list, tuple))}
            if includer or ("source", header, hashlib.sha256(header_bytes).hexdigest()) in read:
                return worker.decode(found["artifact"], worker.MAX_OBJECT)
        raise Held(f"No translation unit on the {side} side compiles {header}; its layout cannot be measured.")

    def tu_compile(header, header_bytes, paths):
        sources, hashes = (dict(item) for item in projections["head"])
        sources[header] = worker.encode(header_bytes)
        wanted = [head_rows[path] for path in paths if path in head_rows]
        wire = json.dumps({"version": 1, "profile": native["profile_sha256"],
                           "source": worker.pack_sources(sources), "plan": wanted},
                          sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        reply = request("compile", wire)
        objects = {}
        for want, value in zip(wanted, reply.get("results", []), strict=True):
            if value.get("tu") != want["tu"] or value.get("status") != "compiled":
                continue
            objects[want["tu"]] = worker.decode(value["artifact"], worker.MAX_OBJECT)
        return objects

    for header in headers:
        try:
            evidence = h5.evidence(header, show(plan["base"], header), show(tip, header), rows, head_objects,
                                   lambda side, data, source: probe_compile(side, data, source, header),
                                   lambda data, paths: tu_compile(header, data, paths))
        except Held as reason:
            evidence = {"layout": {"complete": False, "compatible_shape": False, "atoms": []},
                        "access": {"reads": []}, "held": [str(reason)]}
        result["headers"][header] = evidence
    out = Path(out_root)
    out.mkdir(mode=0o700, exist_ok=True)
    (out / "header-h5.json").write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return result


MAX_RECEIPT = 256 * 1024


def from_compare(plan, native, old, new, work, git, tip, roots, out_root, env=None):
    """Called by ci/managed-check/compare.py behind BFME_H5_EXPORT, inside its comparison. Builds the worker from the
    comparison's own modules (already imported there) and binds the receipt to this run attempt. Returns True when a
    bounded receipt was written."""
    import os
    from types import SimpleNamespace

    import measure
    import pool_protocol
    from common import PIN
    from pool_compare import projection

    env = os.environ if env is None else env
    worker = SimpleNamespace(projection=projection, encode=pool_protocol.encode, decode=pool_protocol.decode,
                             pack_sources=pool_protocol.pack_sources, MAX_OBJECT=pool_protocol.MAX_OBJECT,
                             MAX_SOURCE=pool_protocol.MAX_FILE, profile=PIN["profile_sha256"], roots=roots)
    binding = {"repo": plan["repo"], **{key: plan[key] for key in ("number", "base", "head", "tree")},
               "issuer": {"run": env["GITHUB_RUN_ID"], "attempt": env["GITHUB_RUN_ATTEMPT"]}}
    narrow(plan, native, old, new, work, git, tip, measure.request, worker, out_root, binding)
    receipt = Path(out_root) / "header-h5.json"
    if receipt.stat().st_size > MAX_RECEIPT:
        receipt.unlink()
        return False
    return True
