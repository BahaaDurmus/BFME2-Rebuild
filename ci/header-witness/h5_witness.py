"""H5 typed-use witness: prove that a newly matched function uses one changed class member.

Two extra compiles of the function's own translation unit are compared with its authenticated head object. The two
differ only in which name occupies one slot of the class:

  control  the member stays where it is, and a new member of the same type, `__bfme_w_<tag>`, is added at the end;
  moved    the member is renamed in place to `__bfme_w_<tag>` and re-declared under its own name at the end.

Both variants have the same layout: the same types in the same order, so the same offsets. Each also emits the class
size and alignment as constants, and those must agree. A witness needs all of:

  - the row is newly credited (MATCH with proof), and the head object is its authenticated object;
  - the function in the control compile equals the head object's function (so the appended storage changed nothing
    in it: code that depends on the class size is held);
  - the function differs between control and moved, with the same length and relocations;
  - every differing byte sits in one operand whose control value lies inside the member's offset span from the
    layout atom, and whose moved value is larger by one shift shared by every site.

Anything else holds, by name; it never fails the rule.

Virtual slots work the same way with a declared-only virtual method `__bfme_w_<tag>()` instead of a data member.
Size and base changes, bit-fields, several declarators in one statement, members made by macros, nested classes and
unions are held: this witness cannot speak for them.

The module only transforms protected header bytes and reads compiler objects. It never runs project code. The
compiler runs on the protected worker; this module plans the two header variants and judges the two objects.
"""
import hashlib
import re
import struct

KINDS = ("member", "slot")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
CLASS_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:<[^<>{};]*>)?\Z")
NOT_DATA = re.compile(r"^\s*(?:static|typedef|friend|using|enum|class|struct|union|template|virtual|inline|operator)\b")


class Held(Exception):
    """The witness cannot decide this atom. Its text names what is missing."""


def tag(header, qualified_class, identity):
    return hashlib.sha256(f"{header}\0{qualified_class}\0{identity}".encode()).hexdigest()[:12]


# --- header text ------------------------------------------------------------------------------------------------

def _mask(text):
    """Blank comments, string and character literals and preprocessor lines (same length, newlines kept), so that
    braces and semicolons inside them never count."""
    out, i, n = list(text), 0, len(text)
    line_start = True
    while i < n:
        c = text[i]
        if line_start and c in " \t":
            i += 1
            continue
        if line_start and c == "#":
            j = i
            while j < n and text[j] != "\n":
                if text[j] == "\\" and j + 1 < n and text[j + 1] == "\n":
                    j += 2
                    continue
                j += 1
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i, line_start = j, False
            continue
        line_start = c == "\n"
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            for k in range(i, min(j + 1, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


def _class_body(masked, name):
    """(open, close) of the one definition `class|struct name ... {` ... `}`; held unless exactly one."""
    found = []
    for match in re.finditer(r"\b(class|struct)\s+" + re.escape(name) + r"\b", masked):
        j = match.end()
        while j < len(masked) and masked[j] not in "{;(":
            j += 1
        if j < len(masked) and masked[j] == "{":
            depth = 0
            for k in range(j, len(masked)):
                if masked[k] == "{":
                    depth += 1
                elif masked[k] == "}":
                    depth -= 1
                    if depth == 0:
                        found.append((match.group(1), j, k))
                        break
    if len(found) != 1:
        raise Held(f"Expected one definition of {name} in the header. Found {len(found)}.")
    return found[0]


def _statements(masked, open_, close):
    """Top-level statements of a class body as (start, end_exclusive, access_label_in_force)."""
    out, depth, start, i = [], 0, open_ + 1, open_ + 1
    access = None
    while i < close:
        c = masked[i]
        if c in "{(":
            depth += 1
        elif c in "})":
            depth -= 1
            if depth == 0 and c == "}":
                # an inline body ends the statement unless a ';' follows directly
                j = i + 1
                while j < close and masked[j] in " \t\r\n":
                    j += 1
                if j >= close or masked[j] != ";":
                    out.append((start, i + 1, access))
                    start = i + 1
        elif depth == 0 and c == ";":
            out.append((start, i + 1, access))
            start = i + 1
        elif depth == 0 and c == ":" and masked[i + 1:i + 2] != ":" and masked[i - 1:i] != ":":
            label = masked[start:i].strip()
            if label in ("public", "private", "protected"):
                access = label
                start = i + 1
        i += 1
    return out


def _declared_name(statement):
    """The single declarator name of a plain data member statement, else None."""
    body = statement.strip().rstrip(";").strip()
    if not body or NOT_DATA.match(body) or "(" in body or "," in body or "{" in body or "=" in body:
        return None
    if re.search(r"(?<!:):(?!:)", body):            # bit-field
        return None
    body = re.sub(r"(\s*\[[^\]]*\])+\s*$", "", body)  # array extents
    match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", body)
    return match.group(1) if match else None


def _method_name(statement):
    body = statement.strip()
    match = re.match(r"\s*virtual\b[^(]*?\b([A-Za-z_~][A-Za-z0-9_]*)\s*\(", body)
    return match.group(1) if match else None


def plan(header_bytes, qualified_class, kind, identity, header="", *, abstract_safe=False):
    """Return (control_bytes, moved_bytes) for one layout atom, or raise Held."""
    if kind not in KINDS:
        raise Held(f"A {kind} change needs a witness form this check does not have.")
    if "::" in qualified_class or not CLASS_NAME.fullmatch(qualified_class) or "<" in qualified_class:
        raise Held(f"{qualified_class} is nested or a template; this witness reads plain classes only.")
    if not IDENT.fullmatch(identity):
        raise Held(f"{identity} is not a plain member name.")
    try:
        text = header_bytes.decode("ascii")
    except UnicodeDecodeError:
        raise Held("The header is not plain ASCII.") from None
    masked = _mask(text)
    keyword, open_, close = _class_body(masked, qualified_class)
    hits = []
    for start, end, access in _statements(masked, open_, close):
        statement = masked[start:end]
        name = _declared_name(statement) if kind == "member" else _method_name(statement)
        if name == identity:
            hits.append((start, end, access or ("public" if keyword == "struct" else "private")))
        elif kind == "member" and re.search(r"\b" + re.escape(identity) + r"\b", statement):
            if _declared_name(statement) is None and not statement.strip().startswith(("virtual", "static")) \
                    and "(" not in statement:
                raise Held(f"{qualified_class}::{identity} is declared in a form this check does not read.")
    if len(hits) != 1:
        raise Held(f"Expected one plain declaration of {qualified_class}::{identity}. Found {len(hits)}.")
    start, end, access = hits[0]
    original = text[start:end]
    word = re.compile(r"\b" + re.escape(identity) + r"\b")
    if len(word.findall(masked[start:end])) != 1:
        raise Held(f"{qualified_class}::{identity} appears more than once in its declaration.")
    probe = "__bfme_w_" + tag(header, qualified_class, identity)
    if probe in text:
        raise Held("The probe name already occurs in the header.")
    if kind == "member":
        statement = original.strip()
        renamed = word.sub(probe, statement, count=1)
        tail_control, tail_moved = renamed, statement
    else:
        statement = original.strip()
        if not statement.endswith(";") or "{" in statement or "=" in statement:
            raise Held(f"{qualified_class}::{identity} must be a declared-only virtual method.")
        renamed = f"virtual void {probe}();"
        tail_control, tail_moved = renamed, statement
    after_class = masked.find(";", close)
    if after_class < 0 or masked[close + 1:after_class].strip():
        raise Held(f"{qualified_class} is declared together with a variable; this check does not read that form.")
    if abstract_safe:
        constants = (
            f'\n#pragma pack(push)\n#pragma pack()\n'
            f'struct {probe}_a : {qualified_class} {{ char _t; }};\n'
            f'#pragma pack(pop)\n'
            f'extern "C" const unsigned int {probe}_size = sizeof({qualified_class});\n'
            f'extern "C" const unsigned int {probe}_align = (unsigned int)(sizeof({probe}_a) - sizeof({qualified_class}));\n'
            f'extern "C" const unsigned int {probe}_tail = (unsigned int)(&(({probe}_a *)0)->_t);\n'
        )
    else:
        constants = (f'\nextern "C" const unsigned int {probe}_size = sizeof({qualified_class});'
                     f'\nextern "C" const unsigned int {probe}_align = __alignof({qualified_class});\n')

    def build(slot_text, tail):
        return (text[:start] + " " + slot_text + text[end:close]
                + f"\n{access}:\n    {tail}\n" + text[close:after_class + 1] + constants
                + text[after_class + 1:]).encode("ascii")
    control = build(original.strip(), tail_control)
    moved = build(renamed, tail_moved)
    if control == moved:
        raise Held("The two variants are identical.")
    return control, moved


# --- COFF ---------------------------------------------------------------------------------------------------------

COMDAT = 0x00001000
RELOC_OVERFLOW = 0x01000000


def _coff(data):
    if len(data) < 20:
        raise Held("The compiler object is too short.")
    machine, sections, _, symtab, nsyms, optional, _ = struct.unpack_from("<HHIIIHH", data, 0)
    if machine != 0x14C or optional:
        raise Held("The compiler object is not an i386 COFF object.")
    strings_at = symtab + 18 * nsyms
    def name_of(raw):
        if raw[:4] == b"\0\0\0\0":
            offset = struct.unpack_from("<I", raw, 4)[0]
            end = data.index(b"\0", strings_at + offset)
            return data[strings_at + offset:end].decode("latin-1")
        return raw.rstrip(b"\0").decode("latin-1")
    table = []
    for index in range(sections):
        raw = data[20 + 40 * index:60 + 40 * index]
        size, rawptr, relptr, nrel, flags = (struct.unpack_from("<I", raw, 16)[0], struct.unpack_from("<I", raw, 20)[0],
                                             struct.unpack_from("<I", raw, 24)[0], struct.unpack_from("<H", raw, 32)[0],
                                             struct.unpack_from("<I", raw, 36)[0])
        table.append({"size": size, "raw": rawptr, "rel": relptr, "nrel": nrel, "flags": flags})
    symbols, index = [], 0
    while index < nsyms:
        raw = data[symtab + 18 * index:symtab + 18 * index + 18]
        value, section, kind, klass, aux = struct.unpack_from("<IhHBB", raw, 8)
        symbols.append((index, name_of(raw[:8]), value, section, kind, klass))
        index += 1 + aux
    by_index = {item[0]: item[1] for item in symbols}
    return table, symbols, by_index


def function(data, symbol):
    """The bytes and relocations of one function, by its exact decorated name. Relocation targets are names, so
    they compare across the two objects."""
    table, symbols, names = _coff(data)
    found = [item for item in symbols if item[1] == symbol and item[3] > 0]
    if len(found) != 1:
        raise Held(f"Expected one definition of {symbol} in the object. Found {len(found)}.")
    _, _, value, number, _, _ = found[0]
    section = table[number - 1]
    if section["flags"] & COMDAT:
        start, end = 0, section["size"]
    else:
        later = sorted(item[2] for item in symbols if item[3] == number and item[2] > value)
        start, end = value, (later[0] if later else section["size"])
    body = data[section["raw"] + start:section["raw"] + end]
    relocs = []
    count, first = section["nrel"], 0
    if section["flags"] & RELOC_OVERFLOW:
        # More than 65535 relocations: the first entry's offset field holds the real count (it counts itself).
        count, first = struct.unpack_from("<I", data, section["rel"])[0], 1
    for k in range(first, count):
        offset, target, kind = struct.unpack_from("<IIH", data, section["rel"] + 10 * k)
        if start <= offset < end:
            name = names.get(target, f"#{target}")
            relocs.append((offset - start, kind, name))
    return body, tuple(relocs)


def constant(data, name):
    """The 4-byte value of an emitted extern "C" constant (COFF name with its leading underscore)."""
    table, symbols, _ = _coff(data)
    found = [item for item in symbols if item[1] == "_" + name and item[3] > 0]
    if len(found) != 1:
        raise Held(f"Expected the layout constant {name} in the object. Found {len(found)}.")
    _, _, value, number, _, _ = found[0]
    section = table[number - 1]
    if value + 4 > section["size"] or not section["raw"]:
        raise Held(f"The layout constant {name} has no initialized bytes.")
    return struct.unpack_from("<I", data, section["raw"] + value)[0]


def newly_credited(row):
    before, after = row.get("before") or {}, row.get("after") or {}
    return (before.get("credited") is False and after.get("credited") is True and after.get("proof") is True
            and after.get("status") == "MATCH")


def _shifts(control, moved, low, high):
    """The displacement shift that explains every differing byte, or None. Each run of differing bytes must sit
    inside one 1- or 4-byte little-endian operand whose control value lies in [low, high) (the member's own bytes)
    and whose moved value is that plus one shift shared by every site."""
    runs, k = [], 0
    while k < len(control):
        if control[k] != moved[k]:
            j = k
            while j < len(control) and control[j] != moved[k + (j - k)]:
                j += 1
            runs.append((k, j))
            k = j
        else:
            k += 1
    shift = None
    for s, e in runs:
        if e - s > 4:
            return None
        chosen = None
        for width, form in ((4, "<i"), (1, "<b")):
            for p in range(max(0, e - width), s + 1):
                if p + width > len(control):
                    continue
                vc = struct.unpack_from(form, control, p)[0]
                vm = struct.unpack_from(form, moved, p)[0]
                if low <= vc < high and vm > vc and (shift is None or vm - vc == shift):
                    chosen = vm - vc
                    break
            if chosen is not None:
                break
        if chosen is None:
            return None
        shift = chosen
    return shift, len(runs)


def evaluate(atom, row, head_obj, control_obj, moved_obj, symbol):
    """One access record for the layout join (layout_check), or Held.

    head_obj is the authenticated object of the credited row's own compile. control_obj and moved_obj are the same
    translation unit compiled with plan()'s two header variants, same compiler, profile and flags."""
    if not newly_credited(row):
        raise Held(f"The row at {row.get('rva')} is not newly credited by this change; it witnesses nothing.")
    if hashlib.sha256(head_obj).hexdigest() != row.get("head_object"):
        raise Held("The head object is not the authenticated object of the credited row.")
    probe = "__bfme_w_" + tag(atom["header"], atom["qualified_class"], atom["identity"])
    sizes = [(constant(obj, probe + "_size"), constant(obj, probe + "_align")) for obj in (control_obj, moved_obj)]
    if sizes[0] != sizes[1]:
        raise Held(f"The two variants do not have the same size and alignment: {sizes[0]} and {sizes[1]}.")
    for obj, (sz, al) in zip((control_obj, moved_obj), sizes):
        _, syms, _ = _coff(obj)
        if any(item[1] == "_" + probe + "_tail" and item[3] > 0 for item in syms):
            tail = constant(obj, probe + "_tail")
            if tail != sz or al == 0:
                raise Held(f"Abstract witness alignment probe for {atom['qualified_class']} saw tail={tail} != size={sz}.")
    head, first, second = (function(obj, symbol) for obj in (head_obj, control_obj, moved_obj))
    member = f"{atom['qualified_class']}::{atom['identity']}"
    if head != first:
        raise Held(f"{symbol} changes when storage is appended to {atom['qualified_class']} (it depends on the "
                   "class size); this check cannot isolate a use of " + member + ".")
    if first == second:
        raise Held(f"{symbol} compiles the same with {member} moved: no use of it was observed here.")
    if len(first[0]) != len(second[0]) or first[1] != second[1]:
        raise Held(f"{symbol} changes shape when {member} moves (operand size or relocations); held for review.")
    if any(name.startswith("_" + probe) or name.startswith(probe) for _, _, name in first[1]):
        raise Held("A probe name reached the function's relocations; the variants are not comparable.")
    after = atom.get("after") or {}
    offset, width = after.get("offset"), after.get("width")
    if type(offset) is not int or type(width) is not int or offset < 0 or width <= 0:
        raise Held(f"The layout atom for {member} has no offset and width to check the operands against.")
    found = _shifts(first[0], second[0], offset, offset + width)
    if found is None:
        raise Held(f"{symbol} differs where no operand addresses {member} at offset {offset}; held for review.")
    shift, sites = found
    return {
        "header": atom["header"], "qualified_class": atom["qualified_class"], "kind": atom["kind"],
        "identity": atom["identity"], "receiver_type": atom["qualified_class"], "observed": after,
        "operation": "access", "rva": row["rva"], "path": row["path"], "head_object": row["head_object"],
        "witness": {"form": "W1-swapped-slot-2", "symbol": symbol, "sites": sites, "offset_shift": shift,
                    "size": sizes[0][0], "align": sizes[0][1],
                    "control_sha256": hashlib.sha256(control_obj).hexdigest(),
                    "moved_sha256": hashlib.sha256(moved_obj).hexdigest()},
    }
