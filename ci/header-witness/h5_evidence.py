"""H5 evidence for one changed header: the layout record and the typed-use reads that the layout join consumes.

The compiler is passed in, so this runs unchanged on the protected worker and in tests:

  probe_compile(side, header_bytes, probe_source) -> object bytes
      compile a probe TU with the given bytes standing in for the header on that side ("base" or "head").
  tu_compile(header_bytes, paths) -> {path: object bytes}
      compile the head side's own TUs (their real flags) with the given bytes standing in for the header.

Rows are the native comparison's rows for TUs that include the header: {"rva", "path", "symbol", "head_object",
"before", "after"}; head_objects maps a TU path to its authenticated head object. Nothing here reads a token or
runs project code.
"""
import h5_layout as layout
import h5_witness as witness
from h5_witness import Held

MAX_CLASSES = 4
MAX_MEMBER_ATOMS = 4


def changed_classes(before, after):
    """Class names whose definition text differs. A class defined on only one side is returned too (it holds)."""
    def classes(text):
        if text is None:
            return {}
        masked = witness._mask(text.decode("ascii", "replace"))
        found = {}
        for match in __import__("re").finditer(r"\b(?:class|struct)\s+([A-Za-z_]\w*)\s*(?::[^{;]*)?\{", masked):
            name = match.group(1)
            try:
                _, open_, close = witness._class_body(masked, name)
            except Held:
                found[name] = None
                continue
            found[name] = " ".join(masked[match.start():close + 1].split())
        return found
    old, new = classes(before), classes(after)
    return sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))


def evidence(header, before, after, rows, head_objects, probe_compile, tu_compile):
    """{"layout": {"complete", "compatible_shape", "atoms"}, "access": {"reads": [...]}, "held": [...]}."""
    held, atoms, reads = [], [], []
    names = changed_classes(before, after)
    if len(names) > MAX_CLASSES:
        return {"layout": {"complete": False, "compatible_shape": False, "atoms": []}, "access": {"reads": []},
                "held": [f"{header}: {len(names)} classes changed; this check measures at most {MAX_CLASSES}."]}
    for name in names:
        if before is None or after is None:
            held.append(f"{header}: {name} is new or removed; the declaration rules decide it.")
            continue
        abstract_classes = locals().setdefault("_abstract_classes", set())
        try:
            measured = {}
            inventories = {}
            for side, text in (("base", before), ("head", after)):
                friended, source, inv = layout.probe(text, header, name)
                try:
                    obj = probe_compile(side, friended, source)
                    measured[side] = layout.measure(obj, header, name, inv)
                except Held as first_err:
                    # VC7.1 throws C2259 on __alignof(AbstractClass); retry once with abstract_safe=True.
                    if "not recorded" in str(first_err) or "No translation unit" in str(first_err):
                        raise
                    friended, source, inv = layout.probe(text, header, name, abstract_safe=True)
                    obj = probe_compile(side, friended, source)
                    measured[side] = layout.measure(obj, header, name, inv, abstract_safe=True)
                    abstract_classes.add(name)
                inventories[side] = inv
            verified_overrides = False
            if (inventories["base"]["virtuals"] != inventories["head"]["virtuals"]
                    and inventories["base"]["bases"] == inventories["head"]["bases"]
                    and (measured["base"]["size"], measured["base"]["align"]) ==
                        (measured["head"]["size"], measured["head"]["align"])):
                added_v = layout.added_virtual_overrides(inventories["base"], inventories["head"])
                parsed_v = [layout.parse_virtual_override(s) for s in (added_v or ())]
                if added_v and all(parsed_v):
                    try:
                        v_measured = {}
                        for side, text in (("base", before), ("head", after)):
                            v_hdr, v_src = layout.vslot_probe(text, header, name, parsed_v)
                            v_obj = probe_compile(side, v_hdr, v_src)
                            v_measured[side] = layout.measure_vslots(v_obj, header, name, len(parsed_v))
                        verified_overrides = layout.verify_vslots(v_measured["base"], v_measured["head"])
                    except Held as v_err:
                        if "No translation unit" in str(v_err):
                            raise
                        verified_overrides = False
            record = layout.atoms(header, name, inventories["base"], measured["base"], inventories["head"],
                                  measured["head"], verified_overrides=verified_overrides)
        except Held as reason:
            held.append(str(reason))
            continue
        atoms.extend(record["atoms"])
    members = [atom for atom in atoms if atom["kind"] == "member"]
    if len(members) > MAX_MEMBER_ATOMS:
        held.append(f"{header}: {len(members)} members moved; this check witnesses at most {MAX_MEMBER_ATOMS}.")
        members = []
    credited = [row for row in rows if witness.newly_credited(row)]
    paths = sorted({row["path"] for row in credited})
    for atom in members:
        if not credited:
            break
        try:
            use_abs = atom["qualified_class"] in locals().get("_abstract_classes", set())
            control, moved = witness.plan(after, atom["qualified_class"], "member", atom["identity"], header,
                                          abstract_safe=use_abs)
            control_objs, moved_objs = tu_compile(control, paths), tu_compile(moved, paths)
            if not use_abs and (not control_objs or not moved_objs):
                control, moved = witness.plan(after, atom["qualified_class"], "member", atom["identity"], header,
                                              abstract_safe=True)
                control_objs, moved_objs = tu_compile(control, paths), tu_compile(moved, paths)
        except Held as reason:
            held.append(str(reason))
            continue
        for row in credited:
            try:
                reads.append(witness.evaluate(atom, row, head_objects[row["path"]], control_objs[row["path"]],
                                              moved_objs[row["path"]], row["symbol"]))
                break                                   # one witness per atom is enough
            except (Held, KeyError):
                continue
    complete = not held
    return {"layout": {"complete": complete, "compatible_shape": complete and not atoms, "atoms": atoms},
            "access": {"reads": reads}, "held": held}
