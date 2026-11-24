"""Render source findings for the Actions summary without changing the rules."""

import json
import re


PATH_FIXES = {
    "This path is maintainer-owned or outside the eligible source and header roots.":
        "Remove this path from your pull request and ask a maintainer to handle any required change there.",
    "The path does not follow compiler CI filename limits.":
        "Shorten the path and use plain filename characters with a letter, digit or underscore at the start of each component.",
    "This change adds a source translation unit.":
        "Move the new code into an existing .c or .cpp file and remove this new file.",
    "This change deletes an existing source translation unit.":
        "Restore the deleted .c or .cpp file and make your change in that file.",
    "This change adds a Draft header.":
        "Move the new header out of include/Draft and update any includes of it.",
    "This change deletes a permanent header.":
        "Restore this permanent header before submitting your pull request.",
    "The path has a non-source Git file mode.":
        "Restore this path as a regular, non-executable UTF-8 file.",
    "The blob is not UTF-8.":
        "Save this file as UTF-8 text.",
    "The blob contains a non-text control character or bare CR.":
        "Remove the control character on this line or convert the line ending to LF or CRLF.",
}

TYPE_FIXES = {
    "void pointer": ("void-pointer declarations",
                     "Use the concrete pointer type the function reads or writes, or the field stores. "
                     "Update the shared declaration, definition and callers together. "
                     "If the symbol ledger already records this function with void *, "
                     "keep that signature and ask a maintainer to confirm its ledger entry."),
    "reinterpret cast": ("reinterpret_cast uses",
                         "Give the value its concrete type where it is stored or returned, then use it directly here."),
    "c pointer cast": ("C-style pointer casts",
                       "Declare the pointer with its concrete type where it is supplied, then use it directly here."),
    "this arithmetic": ("offsets from this",
                        "Replace the byte offset with direct access to the typed member or base class."),
    "cast call result": ("casts of return values",
                         "Give the callee and its shared declaration the concrete return type, then use that result directly."),
}


COMPILER_COPY = {
    "A protected compiler directive was added or changed.":
        ("This build-setting line was added or changed.",
         "Remove the line if it is new, or copy the exact original line from main if it replaces an existing setting."),
    "A protected compiler directive was removed (base line).":
        ("This build-setting line is missing. The line number is from main.",
         "Copy the exact line from main back into the file."),
    "Compiler directives changed order.":
        ("The build-setting lines changed order.",
         "Restore their order and exact text from main."),
    "The STLport compiler setting changed.":
        ("This text enables a different C++ library for the file.",
         "Remove the new // stlport text and keep the file's original library setting."),
    "The STLport compiler setting changed (base line).":
        ("The file's original C++ library setting is missing. The line number is from main.",
         "Restore the original // stlport text from main."),
}

FORBIDDEN_FIXES = {
    "inline assembly": "Write the operation in C or C++ using the declared values and functions.",
    "emitted bytes": "Express the operation in C or C++ so the compiler emits its instructions.",
    "naked function": "Use a regular function with the correct signature and C or C++ body.",
    "symbol alias": "Use the existing declaration for the real symbol and call it directly.",
    "symbol alias pragma": "Remove the pragma and use the existing declaration for the real symbol.",
    "linker pragma": "Remove the pragma and ask a maintainer for any required link-setting change.",
    "optimization pragma": "Remove the pragma and keep the file's existing compiler settings.",
    "token pasting that can form a forbidden compiler control":
        "Remove the token paste and express the operation directly in C or C++.",
    "hand-written import declaration":
        "Remove it. Include the SDK or runtime header that declares the function and call it by its own name. "
        "If the file's // cl: line disables _CRTIMP and the original calls through the import table, "
        "the file's setting is wrong: record it in docs/hard_rows.md or file a ledger request.",
    "local body with a runtime name":
        "Give the function the name its evidence supports. A local body cannot be the imported function.",
    "import table reference":
        "Call the function by its own name through its header; the compiler forms the import call.",
    "runtime function declaration":
        "Remove it and include the header that declares the function, such as <string.h>, <stdlib.h> or <windows.h>.",
    "runtime function macro":
        "Remove the macro and call the runtime function under its own name.",
    "library configuration macro":
        "Remove the macro. Runtime and STLport settings belong to the library profile; file a ledger request with the evidence.",
    "repeated include": "Remove the second include of the same header.",
    "nothrow declaration": "Remove __declspec(nothrow) and declare the function as the original header did.",
}


TEXT_ENTITIES = str.maketrans({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    **{char: f"&#{ord(char)};" for char in "[]()*\x60_|\\@#"},
})


def public_text(text):
    return json.dumps(text, ensure_ascii=True)[1:-1].translate(TEXT_ENTITIES)


def contributor_copy(rule, message, remedy):
    if rule == "G1":
        if message in PATH_FIXES:
            return message, PATH_FIXES[message]
        if message.startswith("Function ") and " moved from " in message:
            return message, "Restore the function body to the original source file named above."
        size = re.fullmatch(r"The file exceeds ([0-9]+) bytes\.", message)
        if size:
            return message, f"Reduce this file to at most {size[1]} bytes."
    if rule == "G2" and message in COMPILER_COPY:
        return COMPILER_COPY[message]
    if rule == "G3" and message.startswith("Added ") and message.endswith("."):
        construct = message[6:-1]
        if construct in FORBIDDEN_FIXES:
            return message, FORBIDDEN_FIXES[construct]
        if "compiler pragma" in construct:
            return message, "Remove this pragma and keep the file's existing compiler settings."
    if rule == "G4":
        kind, _, rest = message.partition(" increased by ")
        if kind in TYPE_FIXES and rest:
            label, fix = TYPE_FIXES[kind]
            return f"{label[0].upper() + label[1:]} increased by {rest}", fix
    if rule == "G6":
        scope = re.fullmatch(r"([0-9]+) source files contain real changes beyond Draft include/use updates\. The limit is ([0-9]+)\.",
                             message)
        if scope:
            return (f"This pull request makes substantive edits in {scope[1]} source files.",
                    f"Move this file's edits to a separate pull request so this one changes at most {scope[2]} source files.")
        if message == "A literal include still points to a deleted Draft header.":
            return message, "Point this include at the permanent header or keep the Draft header until all includes are updated."
    if rule == "G8" and message.startswith("New address-shaped identifier "):
        return message, ("Use the identifier already recorded for that address, or ask a maintainer "
                         "to approve a spelling with the address and your evidence.")
    return message, remedy


def public_report(items, rules):
    table = ["| Rule | Result |", "| --- | --- |"]
    findings = []
    for item in items:
        rule = item["id"]
        state = {"pass": "Pass", "fail": "Fail", "blocked": "Fail (blocked by G1)"}[item["status"]]
        label = "Build settings and annotations" if rule == "G2" else rules[rule]
        table.append(f"| {rule} {label} | {state} |")
        findings.extend(item["findings"])
    lines = table
    size = sum(len(line) + 1 for line in lines)
    if findings:
        lines.append("")
    for finding in findings:
        rule, line = finding["rule"], finding["line"]
        if rule not in rules or type(line) is not int or line < 1:
            raise ValueError("Invalid source finding")
        message, remedy = contributor_copy(rule, finding["message"], finding["remedy"])
        path = public_text(finding["path"])
        prose = public_text(message + " " + remedy)
        row = f"- <code>{path}:{line}</code> **{rule}** <span>{prose}</span>"
        size += len(row) + 1
        if size > 949_500:
            lines.append("**More findings:** Split the change into smaller pull requests and check each one again.")
            break
        lines.append(row)
    return "\n".join(lines) + "\n"
