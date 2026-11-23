"""H5 layout atoms: measure one class on both sides of a header change with the protected compiler, no debug flag.

For each side, `probe()` returns the header with one friend line added at the top of the class body (a friend
declaration does not change layout) and a probe translation unit. The probe emits the class size, its alignment,
and the offset and width of every plain data member as initialized constants. `measure()` reads them back from the
compiled object. `atoms()` compares the two sides:

  - a member with the same name on both sides whose offset or width changed: a material member atom (needs a
    witness, see h5_witness.py);
  - members renamed, split or merged so that exactly the same bytes are covered, with the same size and alignment:
    compatible shape (no atom);
  - a member added or removed that does not fit that rule: a material member atom for an added member, a hold for a
    removed one;
  - size or alignment changed: a size atom; virtual functions added, removed or reordered: a slot atom; base classes
    changed: a base atom. The layout join holds these: they need a witness form this check does not have.

The class text is read with the same scanner as h5_witness.py. A statement that could change layout and that this
module cannot read (a macro call that is not a method, a bit-field, several names in one declaration, a member
under a conditional) makes the comparison incomplete when it differs between the sides: that is a hold, never a
pass.
"""
import re
import struct

import h5_witness as witness
from h5_witness import Held

MAX_MEMBERS = 400


def _normal(text):
    return " ".join(text.split())


def inventory(header_bytes, class_name):
    """The class's statements, classified. Raises Held for a class this module cannot read."""
    if "::" in class_name or not witness.CLASS_NAME.fullmatch(class_name) or "<" in class_name:
        raise Held(f"{class_name} is nested or a template; this check reads plain classes only.")
    try:
        text = header_bytes.decode("ascii")
    except UnicodeDecodeError:
        raise Held("The header is not plain ASCII.") from None
    masked = witness._mask(text)
    keyword, open_, close = witness._class_body(masked, class_name)
    head = masked[:open_]
    head_matches = list(re.finditer(rf"\b(?:class|struct)\s+{re.escape(class_name)}\b", head))
    if not head_matches:
        raise Held(f"{class_name} class head could not be parsed.")
    bases = _normal(head[head_matches[-1].end():])
    members, virtuals, other = [], [], []
    for start, end, _ in witness._statements(masked, open_, close):
        statement = _normal(masked[start:end])
        if not statement or statement == ";":
            continue
        name = witness._declared_name(statement)
        if name:
            declared = re.sub(r"\b" + re.escape(name) + r"\b", "", statement, count=1).rstrip(";")
            members.append((name, _normal(declared).replace(" [", "[")))
        elif re.search(r"\bvirtual\b", statement):
            virtuals.append(statement)
        else:
            other.append(statement)
    if len(members) > MAX_MEMBERS:
        raise Held(f"{class_name} has more than {MAX_MEMBERS} members.")
    if len({name for name, _ in members}) != len(members):
        raise Held(f"{class_name} declares a member name twice (a conditional?); this check cannot read it.")
    return {"keyword": keyword, "bases": bases, "members": members,
            "virtuals": virtuals, "other": other, "open": open_}


def _layout_risk(statement, class_name):
    """Could this unreadable statement add, remove or move storage? Methods, typedefs and the like cannot."""
    if re.match(r"(?:static|typedef|friend|using|enum|template|inline|explicit|operator)\b", statement):
        return False
    if re.match(r"(?:class|struct|union)\b", statement) and "{" not in statement:
        return False                                 # a forward declaration
    if re.match(r"[A-Za-z_][\w:<>,\s\*&~]*[\s\*&]~?[A-Za-z_]\w*\s*\(", statement):
        return "(*" in statement                    # a method declaration; a function pointer member is storage
    if re.match(r"~?" + re.escape(class_name) + r"\s*\(", statement):
        return False                                 # constructor or destructor
    return True


def probe(header_bytes, header, class_name, *, abstract_safe=False):
    """(header with the friend line, probe source, inventory) for one side."""
    inv = inventory(header_bytes, class_name)
    if abstract_safe and re.search(r"\bvirtual\b", inv["bases"]):
        raise Held(f"{class_name} uses virtual inheritance; abstract layout alignment cannot be measured safely.")
    tag = witness.tag(header, class_name, "")
    helper = f"__bfme_layout_{tag}"
    text = header_bytes.decode("ascii")
    friended = text[:inv["open"] + 1] + f" friend struct {helper};" + text[inv["open"] + 1:]
    include = header.removeprefix("include/")
    lines = [f'#include "{include}"', "#include <stddef.h>", f"struct {helper} {{"]
    names = ["size", "align"] + (["tail_off"] if abstract_safe else []) + \
            [f"o{i}" for i in range(len(inv["members"]))] + [f"w{i}" for i in range(len(inv["members"]))]
    lines.append("    static const unsigned " + ", ".join(names) + ";")
    lines.append("};")
    lines.append(f"const unsigned {helper}::size = sizeof({class_name});")
    if abstract_safe:
        # VC7.1 rejects __alignof(T) with C2259 on abstract classes. Reset ambient pack,
        # derive one probe struct, and verify tail_off == sizeof(T) to reject indirect virtual bases / EBO.
        a1 = f"__bfme_align1_{tag}"
        lines.extend([
            "#pragma pack(push)",
            "#pragma pack()",
            f"struct {a1} : {class_name} {{ char _bfme_tail_{tag}; }};",
            "#pragma pack(pop)",
            f"const unsigned {helper}::align = (unsigned)(sizeof({a1}) - sizeof({class_name}));",
            f"const unsigned {helper}::tail_off = offsetof({a1}, _bfme_tail_{tag});",
        ])
    else:
        lines.append(f"const unsigned {helper}::align = __alignof({class_name});")
    for i, (name, _) in enumerate(inv["members"]):
        lines.append(f"const unsigned {helper}::o{i} = offsetof({class_name}, {name});")
        lines.append(f"const unsigned {helper}::w{i} = sizeof((({class_name} *)0)->{name});")
    return friended.encode("ascii"), ("\n".join(lines) + "\n").encode("ascii"), inv


def measure(obj, header, class_name, inv, *, abstract_safe=False):
    """Read the probe's constants back from its compiled object."""
    table, symbols, _ = witness._coff(obj)
    helper = f"__bfme_layout_{witness.tag(header, class_name, '')}"
    values = {}
    for _, name, value, number, _, _ in symbols:
        match = re.fullmatch(r"\?(\w+)@" + re.escape(helper) + r"@@2IB", name)
        if match and number > 0:
            section = table[number - 1]
            if not section["raw"] or value + 4 > section["size"]:
                raise Held(f"The layout constant {match.group(1)} of {class_name} is not a compile-time constant "
                           "(a virtual base?).")
            values[match.group(1)] = struct.unpack_from("<I", obj, section["raw"] + value)[0]
    wanted = ["size", "align"] + (["tail_off"] if abstract_safe else []) + \
             [f"{p}{i}" for i in range(len(inv["members"])) for p in "ow"]
    missing = [name for name in wanted if name not in values]
    if missing:
        raise Held(f"The probe of {class_name} did not emit {', '.join(missing[:4])}: not compile-time constants.")
    if abstract_safe and (values["tail_off"] != values["size"] or values["align"] == 0):
        raise Held(f"Abstract alignment probe for {class_name} saw tail_off={values['tail_off']} != size={values['size']} "
                   "(virtual base or empty-base layout); held for review.")
    return {"size": values["size"], "align": values["align"],
            "members": {name: {"offset": values[f"o{i}"], "width": values[f"w{i}"], "type": decl}
                        for i, (name, decl) in enumerate(inv["members"])}}


def _bytes(members, names):
    covered = set()
    for name in names:
        item = members[name]
        covered.update(range(item["offset"], item["offset"] + item["width"]))
    return covered



MAX_VIRTUAL_OVERRIDES = 8
_VOVERRIDE_RE = re.compile(
    r"^virtual\s+(.+?)\s*(\b[A-Za-z_]\w*)\s*\(([^()]*)\)\s*(const)?\s*;$"
)
_VOVERRIDE_CC_RE = re.compile(r"\b(__cdecl|__stdcall|__fastcall)\s*$")
_VOVERRIDE_BAD_RET = re.compile(r"\b(?:virtual|static|inline|__inline|__forceinline|typedef|friend|operator|explicit)\b")


def added_virtual_overrides(before_inv, after_inv):
    """Return newly added virtual declarations if before_inv['virtuals'] is an order-preserving
    subsequence of after_inv['virtuals'] (no removals or reorderings), else None."""
    before_v = before_inv["virtuals"]
    after_v = after_inv["virtuals"]
    if len(after_v) <= len(before_v):
        return None
    added = []
    bi = 0
    for stmt in after_v:
        if bi < len(before_v) and stmt == before_v[bi]:
            bi += 1
        else:
            added.append(stmt)
    if bi != len(before_v) or not added or len(added) > MAX_VIRTUAL_OVERRIDES:
        return None
    return added


def parse_virtual_override(statement):
    """Parse a plain non-pure, non-inline virtual override into (ret, cc, name, params, cv) or None."""
    if any(tok in statement for tok in ("{", "}", "=", "~", "...", "operator", "throw")):
        return None
    m = _VOVERRIDE_RE.match(statement)
    if not m:
        return None
    ret, name, params, cv = m.group(1).strip(), m.group(2), m.group(3).strip(), (m.group(4) or "").strip()
    cc = ""
    cc_m = _VOVERRIDE_CC_RE.search(ret)
    if cc_m:
        cc = cc_m.group(1)
        ret = ret[:cc_m.start()].strip()
    if not ret or _VOVERRIDE_BAD_RET.search(ret) or re.search(r"\b(?:__cdecl|__stdcall|__fastcall)\b", ret):
        return None
    return ret, cc, name, params, cv


def vslot_probe(header_bytes, header, class_name, parsed_overrides):
    """Build (friended_header, probe_source) that takes &Class::method with exact PMF type for each override.
    VC7.1 emits identical function bytes and a ??_9 vcall-thunk relocation on base and head iff every override
    binds to the exact same existing base virtual slot and this-adjustment."""
    inv = inventory(header_bytes, class_name)
    tag = witness.tag(header, class_name, "vslot")
    helper = f"__bfme_vslot_{tag}"
    text = header_bytes.decode("ascii")
    friended = text[:inv["open"] + 1] + f" friend struct {helper};" + text[inv["open"] + 1:]
    include = header.removeprefix("include/")
    lines = [f'#include "{include}"', f"struct {helper} {{"]
    keepers = []
    for i, (ret, cc, name, params, cv) in enumerate(parsed_overrides):
        cc_sp = f"{cc} " if cc else ""
        cv_sp = f" {cv}" if cv else ""
        pmf = f"{ret} ({cc_sp}{class_name}::**out)({params}){cv_sp}"
        pmf_t = f"{ret} ({cc_sp}{class_name}::**)({params}){cv_sp}"
        lines.append(f"    static void v{i}({pmf}) {{ *out = &{class_name}::{name}; }}")
        keepers.append(f"void (*__bfme_vslot_keep_{tag}_{i})({pmf_t}) = &{helper}::v{i};")
    lines.append("};")
    lines.extend(keepers)
    return friended.encode("ascii"), ("\n".join(lines) + "\n").encode("ascii")


def measure_vslots(obj, header, class_name, count):
    """Extract (body_bytes, relocs) for each v<i> probe function from the compiled COFF object."""
    _, symbols, _ = witness._coff(obj)
    helper = f"__bfme_vslot_{witness.tag(header, class_name, 'vslot')}"
    out = []
    for i in range(count):
        prefix = f"?v{i}@{helper}@@"
        matches = [s[1] for s in symbols if s[1].startswith(prefix) and s[3] > 0]
        if len(matches) != 1:
            raise Held(f"Virtual slot probe v{i} for {class_name} did not emit a unique definition.")
        out.append(witness.function(obj, matches[0]))
    return out


def verify_vslots(before_slots, after_slots):
    """True iff every override binds to the exact same VC7.1 ??_9 vcall thunk and bytes on both sides."""
    if not before_slots or len(before_slots) != len(after_slots):
        return False
    for (b_body, b_rels), (a_body, a_rels) in zip(before_slots, after_slots):
        if b_body != a_body or b_rels != a_rels:
            return False
        vcalls = [name for _, _, name in b_rels if name.startswith("??_9")]
        if len(vcalls) != 1:
            return False
    return True


def atoms(header, class_name, before_inv, before, after_inv, after, *, verified_overrides=False):
    """The layout record's body for the layout join: {"complete", "compatible_shape", "atoms"}."""
    def atom(kind, identity, old, new):
        return {"header": header, "qualified_class": class_name, "kind": kind, "identity": identity,
                "before": old, "after": new}
    changed_other = sorted(set(before_inv["other"]) ^ set(after_inv["other"]))
    risky = [s for s in changed_other if _layout_risk(s, class_name)]
    if risky:
        raise Held(f"{class_name}: a changed statement this check cannot read may change layout: {risky[0][:80]}")
    out = []
    if before_inv["bases"] != after_inv["bases"]:
        out.append(atom("base", class_name, before_inv["bases"], after_inv["bases"]))
    if before_inv["virtuals"] != after_inv["virtuals"]:
        same_shape = (before_inv["bases"] == after_inv["bases"]
                      and (before["size"], before["align"]) == (after["size"], after["align"]))
        if not (verified_overrides and same_shape):
            out.append(atom("slot", class_name, len(before_inv["virtuals"]), len(after_inv["virtuals"])))
    if (before["size"], before["align"]) != (after["size"], after["align"]):
        out.append(atom("size", class_name, {"size": before["size"], "align": before["align"]},
                        {"size": after["size"], "align": after["align"]}))
    old, new = before["members"], after["members"]
    for name in sorted(set(old) & set(new)):
        if (old[name]["offset"], old[name]["width"]) != (new[name]["offset"], new[name]["width"]):
            out.append(atom("member", name, old[name], new[name]))
    removed, added = sorted(set(old) - set(new)), sorted(set(new) - set(old))
    if (removed or added) and not (_bytes(old, removed) == _bytes(new, added)
                                   and (before["size"], before["align"]) == (after["size"], after["align"])):
        if removed:
            raise Held(f"{class_name}: {removed[0]} was removed and its storage is not covered by renamed or split "
                       "members; that needs the declaration rules (H4), not a layout witness.")
        for name in added:
            out.append(atom("member", name, None, new[name]))
    return {"complete": True, "compatible_shape": not out, "atoms": out}
