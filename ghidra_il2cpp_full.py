# -*- coding: utf-8 -*-
# @Runtime PyGhidra
#
# IL2CPPDumper -> Ghidra, in one pass:
#   1. parse il2cpp.h into the program's type manager
#   2. detect and mark noreturn runtime helpers
#   3. create every function with the bounds script.json states, plus one at every
#      call target that has none - alternating with
#      step 2 until no new helper turns up, since fixing a boundary is what exposes
#      a helper that was buried inside a merged function
#   4. apply names, string literals, metadata labels
#   5. apply function signatures (needs step 1 to resolve type names)
#   6. name the runtime's shared helpers after what they do
#   7. re-assert the bounds script.json states, after the noreturn flags shrank them
#   8. retype locals from the signatures of the functions they are passed to
#   9. self-check that every method is its own function start
#
# Derived from ghidra_with_struct.py in Il2CppDumper (Perfare, MIT), ported to
# Python 3 with the header parse, noreturn detection, metadata-driven function
# bounds, signature filtering and self-check added.
#
# Two things worth knowing about, both handled below:
#   - il2cpp.h declares inheritance in C++. Ghidra parses C, drops the clause,
#     and every field of a derived class then sits at the wrong offset - so the
#     decompiler prints a NEIGHBOURING field's name and the code reads plausibly
#     but wrong. The clauses are flattened into an explicit first member.
#   - the runtime's shared helpers get a name that says what they DO, because a
#     correct decompilation reading `func_0x0288c014()` six times still tells you
#     nothing. Each small, heavily-called helper has its call chain followed a few
#     hops, and any exception class named along the way becomes its name - the
#     runtime raises by class name, as a plain C string, so the answer is always
#     in there. `il2cpp_throw_NullReferenceException()` at a call site is the same
#     code, now readable as what the C# said: a null check the compiler inserted,
#     not one anybody wrote. See name_runtime_helpers.
#
# Tested with Ghidra 11.3 (PyGhidra) and Il2CppDumper v6.7.46 on an arm64
# libil2cpp.so. Idempotent - if the self-check reports failures, run it again.

import json
import os
import re

import ghidra
from java.io import ByteArrayInputStream

from ghidra.app.cmd.disassemble import DisassembleCommand
from ghidra.app.cmd.function import ApplyFunctionSignatureCmd
from ghidra.app.decompiler import DecompileOptions, DecompInterface
from ghidra.app.util.cparser.C import CParser, CParserUtils
from ghidra.program.model.address import Address, AddressSet
from ghidra.program.model.lang import Register
from ghidra.program.model.listing import FlowOverride
from ghidra.program.model.pcode import HighFunctionDBUtil, PcodeOp
from ghidra.program.model.scalar import Scalar
from ghidra.program.model.symbol import SourceType

# il2cpp.h uses the <stdint.h> types without including it. Ghidra's C parser
# starts with no libc, so it dies on the first int32_t. Pointer-width types are
# mapped to long so this stays correct on both arm32 and arm64.
STDINT_PRELUDE = """
typedef char int8_t;
typedef unsigned char uint8_t;
typedef short int16_t;
typedef unsigned short uint16_t;
typedef int int32_t;
typedef unsigned int uint32_t;
typedef long long int64_t;
typedef unsigned long long uint64_t;
typedef long intptr_t;
typedef unsigned long uintptr_t;
typedef unsigned long size_t;
"""

# il2cpp.h expresses class inheritance in C++: `struct A_Fields : B_Fields {`.
# Ghidra parses C, so it drops the clause and A_Fields loses every field B_Fields
# contributed - which silently shifts all of A's own fields down by sizeof(B) and
# makes the decompiler print a NEIGHBOURING field's name for the right offset.
# Base subobjects are laid out first, in declaration order, so turning them into
# explicit leading members reproduces the layout. Valid in C and C++ both.
#
# Not anchored to a single line, and tolerant of `public`/`protected` and of more
# than one base, because those are all things a different generator version could
# emit - and a regex that quietly matches nothing here would bring the wrong-offset
# bug back looking exactly like correct output.
BASE_CLAUSE = re.compile(r"struct\s+([A-Za-z0-9_]+)\s*:\s*([^{};]+?)\s*\{")
ACCESS_SPEC = re.compile(r"\b(?:public|protected|private)\b")

# Signatures are the slow part: one C prototype parsed per method, ~265k of them,
# which takes hours. Leave empty to do all of them - that is the correct default.
# To iterate on one area, list name substrings and set PARSE_HEADER = False:
#
#   SIG_FILTER = ["GenLevelController", "List<int>", "Dictionary<int, int>"]
#
# Include the generic collections the code calls into, not just the classes you
# are reading - most calls in a Unity method are List/Dictionary accessors, and
# an unsigned callee leaves its arguments untyped at the call site.
SIG_FILTER = []

# Which functions get their locals retyped from the signatures applied above. Empty
# means none, and that is deliberate rather than a default worth changing: unlike every
# other pass here, this one has to decompile each function to see its locals, at roughly
# a tenth of a second each, so all 265180 methods would take days. Point it at the class
# you are about to read.
#
#   RETYPE_FILTER = ["GenLevelController"]
#
# Signatures have to exist first, so a run with APPLY_SIGNATURES = False only works if
# an earlier run applied them.
RETYPE_FILTER = ["GenLevelController"]

# Seconds per function. The big generated methods genuinely need this much; anything
# still going after it was never going to finish.
DECOMPILE_TIMEOUT = 120

# Instantiations that every other instantiation shares. IL2CPP compiles one body for all
# reference-type arguments, so `List<T>_o *` or `List<object>_o *` on a parameter is the
# ABSENCE of evidence about the caller, not evidence. Writing one onto a local is how
# `_items` becomes an object[] and an inlined List<int>.Add degrades from
# `m_Items[i] = v` to raw pointer arithmetic - a step backwards from the untyped local.
GENERIC_SHARED = ("_T__o", "_T1__o", "_T2__o", "_T3__o", "_object__o")

# Local types carrying so little that a class pointer is strictly better. A local already
# typed to a concrete class is never touched - it was either inferred correctly or set by
# hand with Ctrl+L, and clobbering a hand retype would undo the reader's work on every
# re-run.
VAGUE_TYPES = ("Il2CppObject", "Il2CppClass", "void", "undefined", "long", "ulong")

# Set False to skip step 5 outright. Worth it when an earlier run already applied
# the signatures and you are re-running for a later step - otherwise you pay the
# hours again to write the same prototypes a second time.
APPLY_SIGNATURES = False

# Set False if you already parsed il2cpp.h (by hand, or on an earlier run).
PARSE_HEADER = False

# A genuine throw helper sits before hundreds of function starts. A coincidental
# call before one start is not evidence of anything.
MIN_NORET_HITS = 8

# Marking one helper lets analysis split the functions that swallowed the next
# one, so each round can expose more. Stops early once a round finds nothing.
NORET_ROUNDS = 4

# The shape of a helper the adjacency rule cannot see: a few instructions, no
# return anywhere, ending on a call. Kept small so an ordinary short function that
# happens to end on a call to something fallible is not swept up, and paired with
# a caller count, because a genuine throw helper is called from everywhere.
MAX_HELPER_INSNS = 8
MIN_HELPER_XREFS = 8

# How many hops to follow a helper's outgoing calls looking for something that
# names it. The throw thunks reach their exception class in three; much past six
# and the chain has left the helper family and is describing unrelated code.
NAME_CHAIN_DEPTH = 6

# Instructions to decode per hop. These are thunks and small raisers - a body
# longer than this means the walk has wandered into a real function.
NAME_CHAIN_INSNS = 24

# The two helpers the chain walk can never name, because neither of them raises
# anything: the metadata initialiser and the object allocator. They are identified by
# what happens around their call sites instead - see name_codegen_helpers - and from a
# sample, because telling one function from another takes a majority, not a census.
ROLE_SAMPLE = 400
ROLE_SITE_INSNS = 8
MIN_ROLE_SITES = 8

# Bounds a string read, and rejects the binary noise a mis-resolved pointer gives.
MAX_STRING_LEN = 96

# What the runtime passes to its exception raisers: the namespace and the class,
# as two plain C strings, looked up by name at throw time. The class name is the
# whole prize - it is what turns FUN_0288c014() into a sentence.
EXCEPTION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*Exception$")

# Reaching one of these names a helper even when no exception class turned up on
# the way. Keyed by the imported symbol the chain ends at.
CHAIN_TERMINALS = [
    ("__cxa_throw", "il2cpp_throw"),
    ("__cxa_rethrow", "il2cpp_rethrow"),
    ("__cxa_bad_cast", "il2cpp_throw_bad_cast"),
    ("__stack_chk_fail", "il2cpp_stack_check_failed"),
    ("abort", "il2cpp_abort"),
    ("longjmp", "il2cpp_longjmp"),
]

# Name prefixes this script is free to overwrite, on top of anything Ghidra still
# considers a DEFAULT symbol. `il2cpp_noreturn_` is this script's own placeholder
# from an earlier step, and upgrading it is the entire point.
GENERIC_PREFIXES = ("FUN_", "SUB_", "LAB_", "thunk_FUN_", "il2cpp_noreturn_")

# A parameter in script.json can be named `catch` or `static`, because IL permits
# any name even where C# would not, and the C parser sees a keyword. Type keywords
# are deliberately absent: only the parameter-name position is rewritten, and an
# unnamed parameter would put a type keyword in exactly that position.
SIG_KEYWORDS = set([
    "auto", "break", "case", "catch", "class", "continue", "default", "delete",
    "do", "else", "explicit", "export", "extern", "for", "friend", "goto", "if",
    "inline", "mutable", "namespace", "new", "operator", "private", "protected",
    "public", "register", "return", "sizeof", "static", "switch", "template",
    "this", "throw", "try", "typedef", "typename", "using", "virtual", "while",
])
SIG_PARAM_NAME = re.compile(r"(?<=[\s*])([A-Za-z_][A-Za-z0-9_]*)(?=\s*[,)])")

types_ok = not PARSE_HEADER

USER_DEFINED = SourceType.USER_DEFINED
dtm = currentProgram.getDataTypeManager()
fm = currentProgram.getFunctionManager()
listing = currentProgram.getListing()
refs = currentProgram.getReferenceManager()
symtab = currentProgram.getSymbolTable()
baseAddress = currentProgram.getImageBase()

# Every helper accepted so far, and every function start the metadata states.
known_noreturn = set()
metadata_offsets = set()

# Named methods a rule offered up as helpers and accept_helper turned away. Only so
# each is reported once - the same method is reachable from several rules.
refused_methods = set()

# Addresses whose noreturn flag a repair pass cleared, waiting for their callers to be
# re-walked. Queued rather than repaired on the spot because the walk is confined to
# the caller's body, and the bodies are only right after the first bounds pass.
flow_repair_queue = []

# The subset that are named C# methods. Addresses also lists the IL2CPP runtime's
# own C++ in .text, but sparsely - in the game this was written against, 11
# consecutive pairs span 61% of .text and not one .text entry is a method. Treating
# such a pair as one function's extent deletes every real function between them.
# So a stated end requires a method; everything else is only clamped.
method_offsets = set()
TRUST_ENDS = True


def get_addr(addr):
    return baseAddress.add(addr)


def set_name(addr, name):
    try:
        createLabel(addr, name.replace(" ", "-"), True, USER_DEFINED)
    except Exception as e:
        print("set_name failed at %s: %s" % (addr, e))


def set_type(addr, type_name):
    resolved = type_name.replace("*", " *").replace("  ", " ").strip()
    found = getDataTypes(resolved)
    if len(found) == 1:
        target = found[0]
    elif not found and resolved.endswith(" *"):
        base = getDataTypes(resolved[:-2])
        if len(base) != 1:
            return
        target = dtm.addDataType(dtm.getPointer(base[0]), None)
    else:
        return
    try:
        createData(addr, target)
    except Exception:
        pass


def sanitise_signature(sig):
    """Rename any parameter called after a C keyword. Only a token sitting where a
    parameter name goes - after a space or a star, before a comma or the closing
    paren - is touched, so type names are never rewritten."""
    return SIG_PARAM_NAME.sub(
        lambda m: m.group(1) + "_" if m.group(1) in SIG_KEYWORDS else m.group(1),
        sig,
    )


sig_failures = []


def apply_sig(addr, parsed):
    """Returns None on success, else the reason, so set_sig can retry or record it."""
    cmd = ApplyFunctionSignatureCmd(addr, parsed, USER_DEFINED, False, True)
    try:
        if cmd.applyTo(currentProgram):
            return None
        return cmd.getStatusMsg() or "refused, no reason given"
    except Exception as e:
        return "apply: %s" % e


def set_sig(addr, sig, name=None):
    """The prototype is applied under the C name the parser found in the signature.
    Renaming it to the metadata's display name first - `List<int>$$Add`, with its
    spaces, dollars and angle brackets - makes Ghidra reject the symbol and lose the
    whole prototype with it.

    The display name goes back on afterwards, once the prototype is committed, which is
    safe for the same reason step 4 is: set_name writes a label, and a label carries no
    type. Without this a signed run leaves you reading C names, and getting the display
    names back meant a second whole run with APPLY_SIGNATURES = False."""
    try:
        parsed = CParserUtils.parseSignature(
            None, currentProgram, sanitise_signature(sig), False
        )
    except Exception as e:
        sig_failures.append((addr, sig, "parse: %s" % e))
        return
    if parsed is None:
        sig_failures.append((addr, sig, "parse: no signature returned"))
        return
    why = apply_sig(addr, parsed)
    if why is not None:
        # 26010 methods share a name with another one, and a taken name fails the whole
        # command, so retry under the address suffix IDA gives the same clash.
        try:
            parsed.setName("%s_%s" % (parsed.getName(), addr))
        except Exception as e:
            sig_failures.append((addr, sig, "%s / rename: %s" % (why, e)))
            return
        why = apply_sig(addr, parsed)
        if why is not None:
            sig_failures.append((addr, sig, why))
            return
    if name:
        set_name(addr, name)


def flatten_bases(src):
    stats = {"flattened": 0, "multiple": 0, "virtual": 0}

    def rewrite(m):
        name, bases = m.group(1), m.group(2)
        if "virtual" in bases:
            # A virtual base is not at a fixed offset - it is reached through the
            # vtable - so flattening it would invent a layout rather than copy one.
            stats["virtual"] += 1
            return m.group(0)
        parts = [p for p in (ACCESS_SPEC.sub("", b).strip()
                             for b in bases.split(",")) if p]
        if not parts:
            return m.group(0)
        stats["flattened"] += 1
        if len(parts) > 1:
            stats["multiple"] += 1
        members = "".join("\n\tstruct %s __base%s;" % (p, "" if i == 0 else i + 1)
                          for i, p in enumerate(parts))
        return "struct %s {%s" % (name, members)

    return BASE_CLAUSE.sub(rewrite, src), stats


def sanitise_header(src_path):
    """The text of il2cpp.h that Ghidra's C parser can read: the stdint prelude
    prepended, every base-class clause flattened into leading members.

    Returned as a string and parsed from memory. Nothing is written to disk unless
    the parse fails, in which case dump_header puts it somewhere findable."""
    with open(src_path, "r") as src:
        text = src.read()
    text, stats = flatten_bases(text)
    print("header: flattened %d base-class clauses (%d with several bases)"
          % (stats["flattened"], stats["multiple"]))
    print("        inherited fields are now reached as .__base.<field>")
    if stats["virtual"]:
        print("        %d virtual base(s) LEFT ALONE - fields of those classes will"
              % stats["virtual"])
        print("        still be at the wrong offset. Check any you rely on.")
    if not stats["flattened"]:
        print("        nothing matched. Either this header is already plain C, or")
        print("        it declares inheritance in a form this script does not know")
        print("        - in which case every derived class has wrong field offsets.")
    return STDINT_PRELUDE + text


def dump_header(src_path, text):
    """Write the flattened header beside the original, for a failed parse.

    The parse runs from memory, so on failure there would otherwise be no file to
    look at - and a C parser error names a line number in text nobody can see."""
    out_path = os.path.join(os.path.dirname(src_path), "il2cpp_flat.h")
    with open(out_path, "w") as dst:
        dst.write(text)
    return out_path


def find_noreturn(starts):
    """A function start preceded by a call implies that call's target never
    returns - otherwise the compiler would have emitted a return there. Uses
    Ghidra's flow classification rather than mnemonics, so it is not
    architecture-specific."""
    hits = {}
    for ea in starts:
        insn = getInstructionBefore(ea)
        if insn is None or not insn.getFlowType().isCall():
            continue
        # Only counts if the call is immediately adjacent - a gap means padding
        # or data sits between, which says nothing about returning.
        if insn.getMaxAddress().add(1) != ea:
            continue
        flows = insn.getFlows()
        if not flows:
            continue
        target = flows[0]
        key = target.getOffset()
        if key in hits:
            hits[key][1] += 1
        else:
            hits[key] = [target, 1]
    return sorted(hits.values(), key=lambda v: -v[1])


def scan_set(metadata_starts):
    """The addresses the adjacency rule looks at. Metadata covers the generated
    methods only; the IL2CPP runtime's own C++ sits in .text and the analyzer
    found those functions itself. A helper called from there is invisible to a
    scan over method starts alone."""
    seen = set(a.getOffset() for a in metadata_starts)
    out = list(metadata_starts)
    it = fm.getFunctions(True)
    while it.hasNext():
        ep = it.next().getEntryPoint()
        if ep.getOffset() not in seen:
            seen.add(ep.getOffset())
            out.append(ep)
    return sorted(out, key=lambda a: a.getOffset())


def make_code(addr):
    """Disassemble addr if it is not code yet. IL2CPP reaches most methods through
    data tables the analyzer does not follow, so plenty are never disassembled -
    and a function cannot be created on undefined bytes."""
    if getInstructionAt(addr) is not None:
        return True
    try:
        if disassemble(addr):
            return True
    except Exception:
        pass
    # A data item invented by analysis claims the bytes; clear it and retry.
    try:
        clearListing(addr, addr.add(3))
        return bool(disassemble(addr))
    except Exception:
        return False


def called_by_anything(addr):
    it = refs.getReferencesTo(addr)
    while it.hasNext():
        if it.next().getReferenceType().isCall():
            return True
    return False


def create_missing_callees():
    """Create a function at every call target that has none.

    `func_0x0288bd88(...)` in a decompiled body means exactly one thing: there is no
    function at that address. With none there is no prototype and no return
    behaviour, so the call reads as noise and its arguments are lost - which is the
    single most common reason Ghidra output is less readable than IDA's on the same
    binary. IDA creates these itself; Ghidra leaves the bytes as code belonging to
    nobody.

    It also starves every rule in this script. All of them walk the function list,
    so an address with no function cannot be marked noreturn, cannot be reached by
    the adjacency scan, and cannot be named by step 6 - it is not merely unnamed,
    it is invisible. Measured on the game this was written against: the naming pass
    saw 94 candidates in Ghidra against 311 in IDA, and the whole difference was
    helpers sitting in gaps like this one.

    Only the gaps between existing functions are searched, so the reference scan is
    proportional to what is actually missing rather than to the whole 100 MB."""
    gaps = AddressSet()
    for block in getMemoryBlocks():
        if block.isExecute():
            gaps.addRange(block.getStart(), block.getEnd())
    it = fm.getFunctions(True)
    while it.hasNext():
        gaps.delete(it.next().getBody())
    targets = []
    dests = refs.getReferenceDestinationIterator(gaps, True)
    while dests.hasNext():
        addr = dests.next()
        if called_by_anything(addr):
            targets.append(addr)
    created = 0
    monitor.initialize(len(targets))
    monitor.setMessage("Creating missing callees")
    for addr in targets:
        monitor.incrementProgress(1)
        # Re-checked: creating one function can absorb a later target into its body,
        # and that target is then a real part of it rather than a missing function.
        if fm.getFunctionContaining(addr) is not None:
            continue
        if not make_code(addr):
            continue
        try:
            createFunction(addr, None)
        except Exception:
            continue
        if fm.getFunctionAt(addr) is not None:
            created += 1
    print("missing callees: %d created of %d call target(s) with no function"
          % (created, len(targets)))
    return created


def disable_body_fixup():
    """Turn off the analyzer that undoes this script's function bounds.

    Ghidra's "Call-Fixup Installer" (CallFixupAnalyzer) reacts to a function being
    marked noreturn by walking every call to it and, in its own words, repairing the
    damage: it writes FlowOverride.CALL_RETURN onto the calling instruction and then
    calls CreateFunctionCmd.fixupFunctionBody on the CALLER, recomputing that body
    from flow. For IL2CPP a flow-derived body is always too small - the exception
    blocks are entered by the unwinder through the LSDA tables, not by a branch, so no
    flow walk reaches them - and the method loses everything from its first one on.
    Measured here: 13752 methods shrunk, 56411 left with undecoded holes inside them.

    It is a *change* analyzer, so it fires on every program change and its work is
    queued rather than immediate - the queue drains after the script exits. Waiting
    for that queue inside the script was the alternative, and on this binary it did
    not finish in four hours: 56411 redecoded bodies each schedule analysis of their
    own. Turning the analyzer off costs the compiler-spec call-fixups, which for an
    arm64 IL2CPP build is nothing, and makes the bounds this script states final.

    The setting lives in the project, so it stays off for this program until it is
    turned back on by hand in Analysis > Analysis Options."""
    try:
        setAnalysisOption(currentProgram, "Call-Fixup Installer", "false")
    except Exception as e:
        print("could not disable Call-Fixup Installer: %s" % e)
        print("Function bounds will be recomputed from flow after this script exits.")
        print("Turn it off by hand in Analysis > Analysis Options and re-run.")
        return False
    print("disabled the Call-Fixup Installer analyzer: it recomputes a caller's body")
    print("from flow whenever a callee is marked noreturn, which for IL2CPP throws")
    print("away every exception block in the method.")
    return True


def repair_flow(addr):
    """Re-follow the flow a noreturn flag cut off at every call to `addr`.

    Clearing the callee's flag is not enough, and neither is deleting and recreating
    the caller. On deciding a call never returns, Ghidra writes a FlowOverride of
    CALL_RETURN onto the CALLING instruction - "call this, then return" - and stops
    decoding, so the bytes after the call were never turned into code at all. That
    override belongs to the caller's instruction, not to the callee's flag, so
    un-flagging the callee leaves it in place and the caller still decompiles as
    `Dispose(...); return;` with the rest of the method missing.

    It is also not a fall-through override, so clearFallThroughOverride alone does not
    shift it: both have to be reset, and only then does the fall-through address exist
    to disassemble from.

    Measured on GenLevelController$ShuffleItemVisual: the method continues at
    0x2bebd4c and NOTHING branches there - it is reached only by falling through the
    `cbnz` after a call to List.Enumerator<T>::Dispose. That one stale edge hid two
    thirds of the method, including the shuffle the method exists to do."""
    sites = []
    it = refs.getReferencesTo(addr)
    while it.hasNext():
        ref = it.next()
        if ref.getReferenceType().isCall():
            sites.append(ref.getFromAddress())
    repaired = 0
    for site in sites:
        insn = listing.getInstructionAt(site)
        owner = fm.getFunctionContaining(site)
        if insn is None or owner is None:
            continue
        try:
            insn.setFlowOverride(FlowOverride.NONE)
            insn.clearFallThroughOverride()
            after = insn.getFallThrough()
        except Exception:
            continue
        if after is None:
            continue
        # Restricted to the caller's own body. An unrestricted walk from here would
        # run into whatever follows and claim it, merging two methods into one - the
        # exact damage the rest of this script exists to undo.
        DisassembleCommand(after, owner.getBody(), True).applyTo(currentProgram,
                                                                 monitor)
        repaired += 1
    return repaired


def redecode_holes():
    """Decode code missing from inside a function body, whatever stopped the decoder.

    A method's body spans what the metadata states, so undefined bytes INSIDE one mean
    the decoder gave up part way through - and the instruction before the hole says
    why. Where that instruction is a call, its fall-through was deleted because the
    callee was believed never to return.

    This looks for the symptom rather than the flag on purpose. An earlier run may have
    cleared the flag already, and clearing it neither restores the deleted edge nor
    decodes the bytes, so a repair driven only by what is still flagged would never
    fire again and the truncation would survive every future run. It also catches
    truncation this script had no part in - Ghidra's own non-returning-function
    analyzer reaches the same wrong conclusion on IL2CPP throw helpers."""
    exec_set = AddressSet()
    for block in getMemoryBlocks():
        if block.isExecute():
            exec_set.addRange(block.getStart(), block.getEnd())
    monitor.setMessage("Finding truncated bodies")
    holes = listing.getUndefinedRanges(exec_set, True, monitor)
    starts = []
    for rng in holes.getAddressRanges():
        starts.append(rng.getMinAddress())
    repaired = 0
    monitor.initialize(len(starts))
    monitor.setMessage("Redecoding truncated bodies")
    for addr in starts:
        monitor.incrementProgress(1)
        owner = fm.getFunctionContaining(addr)
        if owner is None:
            # Not a hole in a body - a gap between functions, which is what
            # create_missing_callees is for.
            continue
        before = listing.getInstructionBefore(addr)
        if before is None or not before.getFlowType().isCall():
            continue
        try:
            before.setFlowOverride(FlowOverride.NONE)
            before.clearFallThroughOverride()
        except Exception:
            pass
        DisassembleCommand(addr, owner.getBody(), True).applyTo(currentProgram,
                                                               monitor)
        if listing.getInstructionAt(addr) is not None:
            repaired += 1
    return repaired


def repair_queued_flow():
    """Put back the flow a noreturn flag cut, from every direction it can be found.

    Three sources, because the damage outlives its own evidence. The queue is what this
    run un-flagged. refused_methods is what the shape rule offered up and accept_helper
    turned away - a set that matters because a run that already cleared the flags left
    the call-site overrides behind, and the rule still names the same methods even
    though nothing is flagged any more. redecode_holes then covers whatever is still
    sitting as undecoded bytes."""
    targets = set(flow_repair_queue) | refused_methods
    sites = 0
    if targets:
        monitor.initialize(len(targets))
        monitor.setMessage("Re-following truncated flow")
        for addr in targets:
            sites += repair_flow(addr)
            monitor.incrementProgress(1)
    holes = redecode_holes()
    if sites or holes:
        print("flow repair: %d call site(s) re-followed, %d body(ies) redecoded"
              % (sites, holes))
    return sites + holes


def unmark_methods():
    """Clear the noreturn flag from every named method carrying it.

    The guard in accept_helper stops the script creating new false positives, but a
    flag already written to the project outlives the run that wrote it - and while
    it is there, every CALLER of that method still decompiles truncated, with
    "Removing unreachable block" at the top. Re-running a fixed script over a
    project it has already damaged has to undo the damage; otherwise the only cure
    is re-importing the .so, which costs hours."""
    repaired = 0
    monitor.setMessage("Repairing wrongly flagged methods")
    for offset in sorted(method_offsets):
        addr = toAddr(offset)
        f = fm.getFunctionAt(addr)
        if f is None or not f.hasNoReturn():
            continue
        try:
            f.setNoReturn(False)
        except Exception:
            continue
        repaired += 1
        flow_repair_queue.append(addr)
    if repaired:
        print("repaired %d method(s) an earlier run wrongly flagged noreturn"
              % repaired)
    return repaired


def unmark_thin_helpers():
    """Undo this script's own marks on functions that no longer qualify as helpers.

    A function carrying one of this script's names and fewer than MIN_HELPER_XREFS
    callers was marked before that rule required a caller count: a method's cold
    null-check block rather than a shared thunk. The flag and the name both outlive
    the run that wrote them, so the fix has to undo them.

    The name is the only evidence available for who set the flag - Ghidra records
    that a function does not return, not why - which is exactly why this script names
    everything it marks. Reverting the name to DEFAULT also makes the function
    eligible again, so one that does qualify is re-marked on this run."""
    repaired = 0
    entries = []
    it = fm.getFunctions(True)
    while it.hasNext():
        entries.append(it.next().getEntryPoint())
    for addr in entries:
        if addr.getOffset() in method_offsets:
            continue
        f = fm.getFunctionAt(addr)
        if f is None:
            continue
        if not f.getName().startswith(("il2cpp_throw", "il2cpp_noreturn_")):
            continue
        if has_enough_callers(addr):
            continue
        try:
            was_noreturn = f.hasNoReturn()
            if was_noreturn:
                f.setNoReturn(False)
            # Back to FUN_XXXX, so a later round can reconsider it on current rules.
            f.setName(None, SourceType.DEFAULT)
        except Exception:
            continue
        if was_noreturn:
            flow_repair_queue.append(addr)
        repaired += 1
    if repaired:
        print("repaired %d function(s) an earlier run marked as helpers on fewer"
              % repaired)
        print("than %d callers - cold blocks, not shared thunks" % MIN_HELPER_XREFS)
    return repaired


def already_noreturn(addr):
    f = fm.getFunctionAt(addr)
    return f is not None and f.hasNoReturn()


def set_noreturn(addr):
    """Ensure a function exists at addr and is flagged noreturn. True if the flag
    is set once this returns, whether or not this call is what set it.

    That distinction is the whole point. Reporting an already-correct helper as a
    failure is how a re-run over a project this script has already processed ends
    up with an EMPTY record of the helpers: every one is skipped, the rounds report
    nothing, and the naming pass loses the set it is seeded from."""
    f = fm.getFunctionAt(addr)
    if f is None:
        try:
            make_code(addr)
            createFunction(addr, None)
        except Exception:
            return False
        f = fm.getFunctionAt(addr)
    if f is None:
        return False
    if f.hasNoReturn():
        return True
    try:
        f.setNoReturn(True)
        return True
    except Exception:
        return False


def accept_helper(addr, why):
    """Record addr as a helper, flag it noreturn, and name it so a call site reads
    as a throw.

    Returns True only for a helper this run actually had to flag, so a caller
    counting these is counting discoveries and can tell a productive round from a
    repeat. One that was already flagged is still recorded and still counts as
    known - it just is not news."""
    if addr in known_noreturn:
        return False
    if addr.getOffset() in method_offsets:
        # A named C# method is never one of the runtime's anonymous throw thunks.
        # Every throw helper in this binary sits in .text, where not one metadata
        # entry is a method - so a method reaching here is a false positive, and a
        # costly one: the decompiler prunes everything after every call to it, so
        # it is the CALLERS that lose their bodies. That is the exact failure this
        # script exists to prevent, reintroduced from the other side.
        #
        # It happens because fix_bounds cuts a method's end to the next metadata
        # address. Where Addresses has an entry mid-method the body is truncated
        # right after a call, and the shape rule then reads "no return, ends on a
        # call" - a lie this script manufactured itself. boundary_is_known cannot
        # catch it: the boundary IS a metadata start, which is why it was cut there.
        #
        # Measured on the game this was written against: of 17 methods the shape
        # rule offered up, 16 were ordinary - Component::get_tag, set_tag,
        # TaskAwaiter::get_IsCompleted, four constructors. Exactly one,
        # XmlTextReaderImpl::Throw, really never returns. That is the whole cost.
        if addr not in refused_methods:
            refused_methods.add(addr)
            print("  refused %s (%s): a named method, not a runtime helper"
                  % (addr, why))
        return False
    already = already_noreturn(addr)
    if not set_noreturn(addr):
        return False
    known_noreturn.add(addr)
    # Only if it has nothing better. Step 6 derives real names like
    # il2cpp_throw_NullReferenceException, and a re-run must not undo them.
    f = fm.getFunctionAt(addr)
    if f is None or is_generic_name(f):
        set_name(addr, "il2cpp_noreturn_%X"
                 % (addr.getOffset() - baseAddress.getOffset()))
    if already:
        return False
    print("  noreturn: %s (%s)" % (addr, why))
    return True


def restore_noreturn():
    """Re-flag helpers that the bounds pass deleted and rebuilt.

    hasNoReturn is a property of the function, so correcting the bounds of a
    helper that is also a metadata start throws the flag away with the old
    function. Left alone the next round rediscovers it, which reads as progress
    and stops the rounds converging."""
    n = 0
    for addr in sorted(known_noreturn, key=lambda a: a.getOffset()):
        f = fm.getFunctionAt(addr)
        if f is None or f.hasNoReturn():
            continue
        try:
            f.setNoReturn(True)
            n += 1
        except Exception:
            pass
    return n


def tail_call_without_return(f):
    """What f's last instruction calls, if f is a short function that ends on a
    call and contains no return at all - otherwise None. Such a function cannot
    return: control reaches the call and there is nothing after it, so the callee
    must be a throw. A compiler would have emitted a return otherwise."""
    last = None
    n = 0
    it = listing.getInstructions(f.getBody(), True)
    while it.hasNext():
        insn = it.next()
        n += 1
        if n > MAX_HELPER_INSNS:
            return None
        flow = insn.getFlowType()
        if flow.isTerminal():
            return None
        last = insn
    if last is None or not last.getFlowType().isCall():
        return None
    flows = last.getFlows()
    return flows[0] if flows else None


def boundary_is_known(f):
    """Does f really end where Ghidra says? "No return in the body" only means
    something if the body is complete - if analysis stopped at a call, the return
    sits just past the end and the shape is a lie. Evidence that it is complete:
    the next address is a stated method start, or another function begins there."""
    nxt = f.getBody().getMaxAddress().add(1)
    return (nxt.getOffset() in metadata_offsets
            or fm.getFunctionAt(nxt) is not None)


def has_enough_callers(addr):
    """Is addr reached from enough places to be one of the runtime's shared helpers?

    Control flow only - calls and the tail branches these helper chains are built
    from. A pointer to a function in a metadata table is a reference too, and IL2CPP
    is made of such tables, so counting every reference would let a function nothing
    calls pass a test named after callers. Ordinary fall-through is excluded for the
    same reason: it says the previous instruction is adjacent, not that anything
    chose to go here.

    Matches CodeRefsTo(ea, 0) in the IDA script, which is what that count has always
    meant there."""
    it = refs.getReferencesTo(addr)
    n = 0
    while it.hasNext():
        kind = it.next().getReferenceType()
        if not (kind.isCall() or kind.isJump()):
            continue
        n += 1
        if n >= MIN_HELPER_XREFS:
            return True
    return False


def find_thunk_helpers():
    """The helpers the adjacency rule is structurally blind to: a thunk called only
    from inside methods, so no function start ever follows a call to it. Recognised
    by its own shape instead of by what surrounds its call sites."""
    found = 0
    # Snapshotted: accepting a helper re-bounds functions, which mutates the list.
    entries = []
    it = fm.getFunctions(True)
    while it.hasNext():
        entries.append(it.next().getEntryPoint())
    for ea in entries:
        if ea in known_noreturn:
            continue
        f = fm.getFunctionAt(ea)
        if f is None or f.hasNoReturn():
            continue
        try:
            target = tail_call_without_return(f)
        except Exception:
            continue
        if target is None:
            continue
        # Both rules need the caller count, for the same reason. Once the callee is
        # known noreturn, the analyzer ends every function right after a call to it -
        # so "no return in the body" stops being an observation and becomes a
        # consequence of the boundary the flag just moved. Every method's cold
        # null-check block then fits the shape perfectly.
        #
        # Measured on the game this was written against: without the count the IDA
        # port of this rule marked 383 functions in one round and named 379 of them
        # NullReferenceException - the cold blocks of methods, not shared thunks. The
        # binary settles it: 117301 calls reach that helper and not one tail branch
        # does, so not a single one was the tail call the message claimed.
        #
        # A genuine runtime thunk is called from everywhere, which is what the count
        # tests. A cold block belongs to one method and has one reference. The cost
        # is a shared thunk with fewer than MIN_HELPER_XREFS callers, and that is the
        # safe direction to fail: an unmarked helper leaves its own bounds wrong,
        # while a wrongly marked one truncates every CALLER.
        if not has_enough_callers(ea):
            continue
        if target in known_noreturn:
            why = "ends on a call to %s, %d+ callers" % (target, MIN_HELPER_XREFS)
        elif boundary_is_known(f):
            why = "ends on a call with no return, %d+ callers" % MIN_HELPER_XREFS
        else:
            continue
        if accept_helper(ea, why):
            found += 1
    return found


def stated_end(addr):
    """May this pass overrule the analyzer about where the function here ends?"""
    return TRUST_ENDS and addr.getOffset() in method_offsets


def dissolve_merged(starts):
    """A start that sits *inside* a function beginning earlier means analysis
    merged two functions across a noreturn call. Nothing can be created there
    until the merged one is removed."""
    n = 0
    for ea in starts:
        if not stated_end(ea):
            continue
        f = fm.getFunctionContaining(ea)
        if f is not None and f.getEntryPoint() != ea:
            try:
                fm.removeFunction(f.getEntryPoint())
                n += 1
            except Exception:
                pass
    return n


def clear_range(start, end):
    """Delete every function beginning strictly inside (start, end). Creating a
    function fails when the body overlaps an existing one, and a function starting
    mid-method is an analyzer guess - the metadata says the method starts at
    `start`, so the guess is what has to go."""
    victims = []
    it = fm.getFunctions(start, True)
    while it.hasNext():
        ep = it.next().getEntryPoint()
        if ep.compareTo(end) >= 0:
            break
        if not ep.equals(start):
            victims.append(ep)
    n = 0
    for ep in victims:
        try:
            fm.removeFunction(ep)
            n += 1
        except Exception:
            pass
    return n


def create_body(start, body):
    try:
        fm.createFunction(None, start, body, USER_DEFINED)
        return True
    except Exception:
        return False


def split_container(start):
    """Cut back a function that began before `start` and runs past it.

    clear_range cannot help: it removes functions that *begin* inside the range,
    and this one begins outside it. The metadata says a method starts here, so the
    container's end is what is wrong - trim it and the address is free, with the
    earlier function still intact. If the trim is refused the container is removed
    instead, which is what the pass does to merged functions anyway."""
    f = fm.getFunctionContaining(start)
    if f is None or f.getEntryPoint().getOffset() >= start.getOffset():
        return False
    entry = f.getEntryPoint()
    try:
        f.setBody(AddressSet(entry, start.subtract(1)))
        return True
    except Exception:
        pass
    try:
        fm.removeFunction(entry)
        return True
    except Exception:
        return False


def make_function(start, end):
    """Create [start, end), escalating through the reasons creation can fail."""
    body = AddressSet(start, end.subtract(1))
    f = fm.getFunctionAt(start)
    if f is not None:
        try:
            f.setBody(body)
            return "kept"
        except Exception:
            try:
                fm.removeFunction(start)
            except Exception:
                return "failed"
    if create_body(start, body):
        return "created"
    if make_code(start) and create_body(start, body):
        return "undefined first"
    if clear_range(start, end) and create_body(start, body):
        return "overlap cleared"
    if split_container(start) and create_body(start, body):
        return "container split"
    try:
        # Last resort: let the analyzer choose the bounds. Wrong bounds still beat
        # no function at all, since the decompiler needs one to work on.
        if createFunction(start, None) is not None:
            return "flow-inferred"
    except Exception:
        pass
    return "failed"


def make_function_clamped(start, end):
    """Used where `end` is not evidence of a boundary: either script.json has no
    Addresses array, or this start is not a named method. Then `end` is only where
    the next entry point begins, which is not the same as where this function ends,
    so it is a ceiling - let the analyzer pick the bounds and only cut a function
    that overran. Nothing here removes or grows an existing function."""
    f = fm.getFunctionAt(start)
    outcome = "kept"
    if f is None:
        if not make_code(start):
            return "failed"
        try:
            createFunction(start, None)
        except Exception:
            return "failed"
        f = fm.getFunctionAt(start)
        if f is None:
            return "failed"
        outcome = "created"
    if f.getBody().getMaxAddress().compareTo(end) >= 0:
        try:
            f.setBody(AddressSet(start, end.subtract(1)))
        except Exception:
            pass
    return outcome


def fix_bounds(starts, label):
    monitor.initialize(len(starts))
    monitor.setMessage("Dissolving merged functions")
    dissolved = dissolve_merged(starts)

    tally = {}
    monitor.initialize(len(starts) - 1)
    monitor.setMessage("Function bounds")
    for i in range(len(starts) - 1):
        start, end = starts[i], starts[i + 1]
        how = make_function(start, end) if stated_end(start) else \
            make_function_clamped(start, end)
        tally[how] = tally.get(how, 0) + 1
        monitor.incrementProgress(1)
    restored = restore_noreturn()
    print("bounds %s: %d merged dissolved, %s"
          % (label, dissolved,
             ", ".join("%s %d" % (k, v) for k, v in sorted(tally.items()))))
    if restored:
        print("  re-flagged %d noreturn helper(s) rebuilt by this pass" % restored)
    return tally.get("failed", 0)


def assert_stated_bounds(starts):
    """Re-assert the bounds the metadata states, after everything that can undo them.

    Marking a callee noreturn makes Ghidra repair the flow at every call site, and
    repairing includes recomputing the CALLER's body from flow. So a body set from the
    metadata is shrunk again by the next noreturn flag - including a correct one, and
    including the flags restore_noreturn re-applies at the end of the bounds pass that
    just set the body. The pass undoes its own work.

    What is left is a body derived from flow, which for IL2CPP is always too small: a
    method's exception-handling blocks are entered from the unwinder through the LSDA
    tables, not from a branch, so no flow walk can reach them and everything past the
    first one falls out of the function.

    Measured on GenLevelController$$ShuffleItemVisual: 1000 bytes in 8 fragments out of
    a stated 2952, every fragment ending on the byte after a call treated as terminal,
    and the whole tail of the method - the shuffle it exists to do - decoded but
    belonging to no function at all, which puts it out of the decompiler's reach
    whatever the flow says.

    Nothing shrinks them again after this pass, because disable_body_fixup turned off
    the analyzer that would - without that this pass is pointless, since the shrinking
    is queued and happens once the script has exited, where no pass can answer it. That
    is why an earlier version reported `stated bounds: 0 method(s) widened` next to a
    truncated decompilation and the two were not a contradiction.

    Runs dead last and only widens: nothing here creates, removes, names or flags."""
    widened = 0
    monitor.initialize(len(starts) - 1)
    monitor.setMessage("Re-asserting stated bounds")
    for i in range(len(starts) - 1):
        monitor.incrementProgress(1)
        start, end = starts[i], starts[i + 1]
        if not stated_end(start):
            continue
        f = fm.getFunctionAt(start)
        if f is None:
            continue
        want = AddressSet(start, end.subtract(1))
        # Whether the extent is covered, not whether the byte count matches: a body of
        # the right size in eight fragments with the gaps in the middle is the exact
        # shape this pass exists to correct.
        if f.getBody().contains(want):
            continue
        try:
            f.setBody(want)
            widened += 1
        except Exception:
            pass
    print("stated bounds: %d method(s) widened back to their metadata extent"
          % widened)
    return widened


def read_c_string(addr):
    """A NUL-terminated printable string at addr, or None.

    Read byte by byte rather than through a defined Data object: these pointers
    land in a blob analysis never typed, so there is no string there to fetch -
    only bytes that happen to spell one."""
    out = []
    for i in range(MAX_STRING_LEN):
        try:
            b = getByte(addr.add(i)) & 0xFF
        except Exception:
            return None
        if b == 0:
            return "".join(out) or None
        if b < 0x20 or b > 0x7E:
            return None
        out.append(chr(b))
    return None


def operand_number(insn, op):
    """The constant an operand denotes, whether the disassembler rendered it as a
    scalar or as an address. Registers are skipped rather than read - a Register
    also answers getOffset(), with its slot in the register space."""
    for obj in insn.getOpObjects(op):
        if isinstance(obj, Address):
            return obj.getOffset()
        if isinstance(obj, Scalar):
            return obj.getUnsignedValue()
    return None


def operand_register(insn, op):
    for obj in insn.getOpObjects(op):
        if isinstance(obj, Register):
            return obj.getName()
    return None


def paired_page_address(insn):
    """The address an adrp/add pair builds, or None.

    A 64-bit address does not fit in one AArch64 instruction, so every string
    pointer arrives as a page load plus an offset. Ghidra usually leaves a
    reference to follow - but only where it typed the destination, and the string
    blob these point into is often left as raw bytes with no reference at all.
    Gated on the mnemonics, so on any other architecture it never fires."""
    if insn.getMnemonicString().lower() != "add":
        return None
    prev = insn.getPrevious()
    if prev is None or prev.getMnemonicString().lower() != "adrp":
        return None
    reg = operand_register(prev, 0)
    # Both operands of the add must be the register the adrp just loaded,
    # otherwise the pair is unrelated and the sum is meaningless.
    if reg is None or operand_register(insn, 0) != reg \
            or operand_register(insn, 1) != reg:
        return None
    page = operand_number(prev, 1)
    offset = operand_number(insn, 2)
    if page is None or offset is None or page <= 0:
        return None
    try:
        return toAddr(page + offset)
    except Exception:
        return None


def strings_at(insn):
    """Every plausible C string the instruction points at."""
    out = []
    for ref in insn.getReferencesFrom():
        if not ref.getReferenceType().isData():
            continue
        s = read_c_string(ref.getToAddress())
        if s:
            out.append(s)
    if not out:
        built = paired_page_address(insn)
        if built is not None:
            s = read_c_string(built)
            if s:
                out.append(s)
    return out


def name_at(addr):
    """The most meaningful name for an address, resolving through a thunk.

    Ghidra represents an imported function as a PLT thunk pointing at a stub in
    EXTERNAL space, and the thunk itself is often still called thunk_FUN_xxx. The
    name worth having - __cxa_throw - belongs to what it points AT."""
    f = fm.getFunctionAt(addr)
    if f is not None:
        if f.isThunk():
            target = f.getThunkedFunction(True)
            if target is not None:
                return target.getName()
        return f.getName()
    sym = symtab.getPrimarySymbol(addr)
    return sym.getName() if sym is not None else ""


def transfer_names(insn):
    """Names of everything this instruction hands control to, by reference rather
    than by flow.

    getFlows() returns code flows inside the program, and an imported function is
    not in the program - it lives in EXTERNAL space. So a call to __cxa_throw has a
    call REFERENCE but no flow, and a walk that only follows flows dies on the last
    hop, one step short of the one symbol that would have named the whole chain.
    That is the difference between the two ports: IDA gives the PLT stub a real name
    in the listing, Ghidra keeps it in a separate space that has to be asked for."""
    out = []
    for ref in insn.getReferencesFrom():
        kind = ref.getReferenceType()
        if not (kind.isCall() or kind.isJump()):
            continue
        to = ref.getToAddress()
        if to is None:
            continue
        name = name_at(to)
        if name:
            out.append(name)
    return out


def transfer_target(insn):
    """Where the instruction hands control to other code, if anywhere: the callee
    of a call, or the destination of an unconditional jump. Jumps count as much as
    calls - these helper chains are built out of tail branches."""
    flow = insn.getFlowType()
    if not (flow.isCall() or (flow.isJump() and not flow.isConditional())):
        return None
    flows = insn.getFlows()
    return flows[0] if flows else None


def walk_limit(addr):
    """The last address a linear walk from addr may reach: the end of its function,
    or just before the next function start if it has none. None means unbounded.

    Load-bearing, not tidiness. A call does not stop flow, so without a limit the
    walk leaves an 8-byte throw thunk through its own call and carries straight on
    into the NEXT thunk, which raises a different exception - and then names the
    first one after the second's class. These thunks sit in a packed row, so that
    is the normal case rather than an edge one."""
    f = fm.getFunctionContaining(addr)
    if f is not None:
        return f.getBody().getMaxAddress()
    # Iterated rather than asked for directly: getFunctionAfter lives on Listing,
    # not on FunctionManager, and getFunctions(start, forward) is on both.
    for nxt in fm.getFunctions(addr, True):
        entry = nxt.getEntryPoint()
        if entry.compareTo(addr) > 0:
            return entry.subtract(1)
    return None


def note_terminals(names, terminals):
    """Record the role for any of `names` that is a chain terminal.

    Only a leading DOT is stripped - some loaders name an import stub `.__cxa_throw`.
    Stripping underscores as well looks equally harmless and is not: every symbol
    here begins with two of them, so `__cxa_throw` becomes `cxa_throw`, matches
    nothing in CHAIN_TERMINALS, and the whole terminal rule silently never fires.
    That one extra character in a strip set is why this port named 0 helpers by
    terminal where the IDA one named 55."""
    for name in names:
        found = (name or "").lstrip(".")
        for symbol, role in CHAIN_TERMINALS:
            if found.startswith(symbol):
                terminals.append(role)


def chain_evidence(start):
    """Walk outwards from `start` through calls and tail branches, collecting the
    C strings and the named terminals reached. Breadth-first, so strings arrive
    nearest-first and the shallowest exception class is the one that wins."""
    seen = set([start])
    strings = []
    terminals = []
    frontier = [start]
    for _hop in range(NAME_CHAIN_DEPTH):
        if not frontier:
            break
        nxt = []
        for addr in frontier:
            note_terminals([name_at(addr)], terminals)
            insn = listing.getInstructionAt(addr)
            limit = walk_limit(addr)
            for _i in range(NAME_CHAIN_INSNS):
                if insn is None:
                    break
                if limit is not None and insn.getAddress().compareTo(limit) > 0:
                    break
                strings.extend(strings_at(insn))
                note_terminals(transfer_names(insn), terminals)
                target = transfer_target(insn)
                if target is not None and target not in seen:
                    seen.add(target)
                    nxt.append(target)
                flow = insn.getFlowType()
                if flow.isTerminal() or (flow.isJump() and not flow.isConditional()):
                    break
                insn = insn.getNext()
        frontier = nxt
    return strings, terminals


def helper_role(strings, terminals):
    """A name saying what a helper does, from the evidence chain_evidence collected,
    or None to leave its name alone.

    The one thing an IL2CPP throw thunk always does is name its exception class,
    because the runtime looks the class up by string when it raises. Following the
    chain to that string is what turns `func_0x0288c014()` at a call site into
    `il2cpp_throw_NullReferenceException()` - and a compiler-inserted null check
    into something a reader recognises as one.

    Takes the evidence rather than an address so the caller can report on what was
    found when nothing names it."""
    for s in strings:
        if EXCEPTION_NAME.match(s):
            return "il2cpp_throw_" + s
    return terminals[0] if terminals else None


def is_generic_name(f):
    """Is this function's name one the script may overwrite? DEFAULT covers every
    name Ghidra invented itself; the prefixes cover this script's own placeholder,
    which is USER_DEFINED and so would otherwise look authoritative."""
    sym = f.getSymbol()
    if sym is None or sym.getSource() == SourceType.DEFAULT:
        return True
    return f.getName().startswith(GENERIC_PREFIXES)


def call_target(insn):
    """Where this instruction calls, if it calls."""
    if insn is None or not insn.getFlowType().isCall():
        return None
    for ref in insn.getReferencesFrom():
        if ref.getReferenceType().isCall():
            return ref.getToAddress()
    return None


def call_after(insn):
    for _ in range(ROLE_SITE_INSNS):
        if insn is None:
            return None
        target = call_target(insn)
        if target is not None:
            return target
        insn = insn.getNext()
    return None


def call_before(insn):
    for _ in range(ROLE_SITE_INSNS):
        if insn is None:
            return None
        insn = insn.getPrevious()
        target = call_target(insn)
        if target is not None:
            return target
    return None


def code_refs_to(addr, want_call):
    """The instructions referring to addr - calls to a function, or the page-pair
    loads that take the address of a metadata slot."""
    out = []
    it = refs.getReferencesTo(addr)
    while it.hasNext():
        ref = it.next()
        if want_call and not ref.getReferenceType().isCall():
            continue
        insn = listing.getInstructionAt(ref.getFromAddress())
        # A metadata slot is pointed at by other metadata tables too. Those refs come
        # from data, so there is no instruction, and they say nothing about callers.
        if insn is not None:
            out.append(insn)
    return out


def agreed_target(sites, pick):
    """The address most of these call sites agree on, its share, and the runners-up.

    A vote rather than a rule: no single site proves what a helper is, but a few
    hundred sites all reaching the same address does."""
    tally = {}
    for insn in sites:
        target = pick(insn)
        if target is not None:
            tally[target] = tally.get(target, 0) + 1
    if not tally:
        return None, 0, []
    ranked = sorted(tally, key=lambda a: (-tally[a], a.getOffset()))
    return ranked[0], tally[ranked[0]], [(a, tally[a]) for a in ranked[1:3]]


def sample_sites(addresses, want_call):
    sites = []
    for addr in addresses:
        sites.extend(code_refs_to(addr, want_call))
        if len(sites) >= ROLE_SAMPLE:
            return sites[:ROLE_SAMPLE], True
    return sites, False


def name_codegen_helpers():
    """Name the two helpers the chain walk cannot: neither one raises anything, so
    there is no exception class anywhere in their call trees to name them after.

    They are the two most-called functions in the binary and the reason an IL2CPP
    method is unreadable before this runs - every method opens with a block of them:

        FUN_0288bd88(&Method$List<int>.Add);        <- initialize_method_metadata
        obj = FUN_028c8834(List<int>_TypeInfo);     <- object_new
        List<int>$$.ctor(obj, ...);                 <- and this is what `new` is

    Neither can be recognised from its own body, so each is identified by what its
    call sites do. Whatever gets called right after a Method$ slot is loaded is the
    metadata initialiser; whatever gets called right before a constructor is the
    allocator. Both are decided by majority over a sample of sites."""
    found = {}
    slots = [get_addr(m["Address"]) for m in data.get("ScriptMetadataMethod", [])]
    ctors = [get_addr(m["Address"]) for m in data.get("ScriptMethod", [])
             if m["Name"].endswith("$$.ctor")]
    for role, addresses, want_call, pick in (
        ("il2cpp_codegen_initialize_method_metadata", slots, False, call_after),
        ("il2cpp_codegen_object_new", ctors, True, call_before),
    ):
        sites, capped = sample_sites(addresses, want_call)
        addr, hits, runners_up = agreed_target(sites, pick)
        if addr is None or hits < MIN_ROLE_SITES:
            print("  %s: no agreement over %d site(s)" % (role, len(sites)))
            continue
        f = fm.getFunctionAt(addr)
        if f is None:
            print("  %s: %s agreed by %d site(s), but no function there"
                  % (role, addr, hits))
            continue
        print("  %s: %s, %d of %d site(s)%s" % (
            role, addr, hits, len(sites), " (sampled)" if capped else ""))
        for other, count in runners_up:
            print("      runner-up %s x%d" % (other, count))
        # A thunk is what the call sites hit, but the body is what a reader lands on
        # after following it, so name both - Ghidra shows the thunk as thunk_<name>.
        for target in (f, f.getThunkedFunction(True)):
            if target is not None and is_generic_name(target):
                set_name(target.getEntryPoint(), role)
        # The function the call sites reach, not the body behind it, because the next
        # pass walks call sites too and they refer to whichever one the vote found.
        found[role] = f
    return found


def data_refs_from(insn):
    """Every data address this instruction points at."""
    out = []
    for ref in insn.getReferencesFrom():
        if ref.getReferenceType().isData():
            out.append(ref.getToAddress())
    if not out:
        built = paired_page_address(insn)
        if built is not None:
            out.append(built)
    return out


def slot_loaded_before(insn, known):
    """The one metadata slot this call site loads that nothing has named yet.

    Walks back only as far as the previous call, so a slot belonging to an earlier
    statement cannot leak in, and gives up as soon as a second candidate turns up.
    Two candidates means guessing which register it went to, and a confidently wrong
    name is worse to read than the DAT_ it replaced."""
    found = None
    for _ in range(ROLE_SITE_INSNS):
        insn = insn.getPrevious()
        if insn is None or insn.getFlowType().isCall():
            break
        for addr in data_refs_from(insn):
            if addr.getOffset() in known:
                continue
            if found is not None and found != addr:
                return None
            found = addr
    return found


def name_type_slots(object_new):
    """Name the class slots that `new` allocates from.

    Il2CppDumper's ScriptMetadata table names type slots, but not the ones belonging
    to inflated generics. On the binary this was written against it covers 0x6131580
    upward, while the List<int> and Dictionary<int,int> slots sit below that, so they
    survive every earlier pass as bare DAT_ addresses:

        obj = il2cpp_codegen_object_new(DAT_0622e078);
        System.Collections.Generic.List<int>$$.ctor(obj, ...);

    The call site says what the slot is regardless. Whatever object_new was handed,
    the constructor called straight afterwards names its class - so the second line
    is what names the first. The constructor's own symbol is not read for this: it
    may carry a label, a C name from an applied signature, or nothing at all,
    depending on the filters this run used. Its address in ScriptMethod is stable."""
    if object_new is None:
        return 0
    suffix = "$$.ctor"
    ctor_class = {}
    for m in data.get("ScriptMethod", []):
        if m["Name"].endswith(suffix):
            ctor_class[get_addr(m["Address"]).getOffset()] = m["Name"][:-len(suffix)]
    known = set()
    for table in ("ScriptMetadataMethod", "ScriptMetadata"):
        known.update(get_addr(m["Address"]).getOffset() for m in data.get(table, []))
    named = 0
    for insn in code_refs_to(object_new.getEntryPoint(), True):
        target = call_after(insn.getNext())
        if target is None:
            continue
        cls = ctor_class.get(target.getOffset())
        if cls is None:
            continue
        slot = slot_loaded_before(insn, known)
        if slot is None:
            continue
        sym = currentProgram.getSymbolTable().getPrimarySymbol(slot)
        if sym is not None and sym.getSource() != SourceType.DEFAULT:
            continue
        set_name(slot, cls + "_TypeInfo")
        # So the thousands of later sites allocating the same class stop at the
        # cheap check above instead of resolving a symbol each time.
        known.add(slot.getOffset())
        named += 1
    return named


def name_runtime_helpers():
    """Give the runtime's shared helpers names that say what they do.

    Every other pass here is about getting the CODE right. This one is about
    getting it READABLE, which is a separate problem: a method whose body is
    `func_0x0288c014()` six times reads as noise, and the same method reading
    `il2cpp_throw_NullReferenceException()` six times reads as the C# that was
    actually written - where those throws are null checks nobody typed by hand.

    Candidates are the helpers already known noreturn, plus any small function
    with enough callers to be a shared one. A chain walk over all 265k methods
    would cost far more and tell us nothing: those already have real names."""
    candidates = dict((a, True) for a in known_noreturn)
    # 4 bytes per AArch64 instruction, doubled for slack on variable-length ISAs.
    limit = MAX_HELPER_INSNS * 8
    it = fm.getFunctions(True)
    while it.hasNext():
        f = it.next()
        addr = f.getEntryPoint()
        if addr in candidates or addr.getOffset() in method_offsets:
            continue
        if f.getBody().getNumAddresses() > limit or not is_generic_name(f):
            continue
        if has_enough_callers(addr):
            candidates[addr] = True
    roles = {}
    # Why the rest were not named. Without this a report of "named 0" is indefinite:
    # it could mean every helper already has a name, or that the chain walk is broken
    # and finding nothing at all. Those need opposite responses.
    outcome = {"already named": 0, "no evidence": 0, "strings, no class": 0}
    sample = []
    monitor.initialize(len(candidates))
    monitor.setMessage("Naming runtime helpers")
    for addr in sorted(candidates, key=lambda a: a.getOffset()):
        monitor.incrementProgress(1)
        f = fm.getFunctionAt(addr)
        if f is None:
            continue
        if not is_generic_name(f):
            outcome["already named"] += 1
            continue
        strings, terminals = chain_evidence(addr)
        role = helper_role(strings, terminals)
        if role is None:
            key = "strings, no class" if strings else "no evidence"
            outcome[key] += 1
            if len(sample) < 3:
                sample.append((addr, strings[:4]))
            continue
        set_name(addr, role)
        roles[role] = roles.get(role, 0) + 1
    total = sum(roles.values())
    print("named %d of %d runtime helper(s) by role" % (total, len(candidates)))
    print("  not named: %s" % ", ".join(
        "%s %d" % (k, v) for k, v in sorted(outcome.items()) if v))
    for addr, strings in sample:
        print("  e.g. %s saw %s" % (addr, strings if strings else "nothing"))
    for role in sorted(roles, key=lambda r: (-roles[r], r))[:12]:
        print("  %s x%d" % (role, roles[role]))
    return total


def is_generic_shared(name):
    return any(tag in name for tag in GENERIC_SHARED)


def class_pointer(dt):
    """The type itself when it points at one concrete IL2CPP class, else None.

    Il2CppDumper names every object struct `<Class>_o`, so the suffix is what separates a
    class pointer from `Il2CppObject *`, `MethodInfo *` and the primitives - none of which
    tell you which class a local holds."""
    if dt is None:
        return None
    name = dt.getName()
    if not name.endswith("*"):
        return None
    inner = name.rstrip(" *")
    if not inner.endswith("_o") or is_generic_shared(inner):
        return None
    return dt


def worth_replacing(dt):
    """Whether a class pointer would tell the reader more than the local's current type."""
    if dt is None:
        return True
    name = dt.getName()
    # A fixed-size stack slot is the enumerator case: three scalars sharing 24 bytes,
    # where writing a struct over one of them overlaps its neighbours. Ghidra's variable
    # recovery split them and no retype puts them back, so leave them to Ctrl+L.
    if "[" in name:
        return False
    # A pointer needs the width of one. Anything narrower is not holding an object.
    if dt.getLength() not in (0, 8):
        return False
    inner = name.rstrip(" *")
    return is_generic_shared(inner) or any(inner.startswith(v) for v in VAGUE_TYPES)


def local_votes(hf):
    """One vote per call argument: the callee's declared parameter type, cast at the local
    that was passed there.

    Every vote in the function is collected before anything is written, because a local
    reaching two different classes is the one case where retyping actively hurts. That
    local is two unrelated variables Ghidra merged into one slot, so either type is wrong
    for half its uses - the `((pIVar7[1].klass)->_1).name` shape. Those are counted and
    skipped; they stay a right-click Split Out As New Variable away from readable."""
    votes = {}
    for op in hf.getPcodeOps():
        if op.getOpcode() != PcodeOp.CALL:
            continue
        target = op.getInput(0).getAddress()
        if target is None:
            continue
        callee = fm.getFunctionAt(target)
        if callee is None:
            continue
        if callee.isThunk():
            callee = callee.getThunkedFunction(True) or callee
        for i, param in enumerate(callee.getParameters()):
            if i + 1 >= op.getNumInputs():
                break
            want = class_pointer(param.getDataType())
            if want is None:
                continue
            high = op.getInput(i + 1).getHigh()
            if high is None:
                continue
            sym = high.getSymbol()
            if sym is None or sym.isParameter() or sym.isGlobal():
                continue
            if not worth_replacing(high.getDataType()):
                continue
            votes.setdefault(sym.getName(), (sym, {}))[1][want.getName()] = want
    return votes


def retype_locals():
    """Push each callee's parameter types back onto the caller's locals.

    Ghidra will not do this itself. It types a local from what the bytes do, so a pointer
    that is only ever loaded from and handed to another function stays `Il2CppObject *`,
    and every field read through it prints a NEIGHBOURING struct's field name - code that
    reads plausibly and says the wrong thing. Hex-Rays propagates a callee's `__this` type
    back automatically, which is most of why its output on this binary reads better.

    Scoped to RETYPE_FILTER because seeing a function's locals means decompiling it. That
    is the only pass here that costs per-function time rather than per-binary time."""
    if not RETYPE_FILTER:
        print("locals: RETYPE_FILTER is empty, none retyped.")
        return
    wanted = {}
    for m in methods:
        if not any(k in m["Name"] for k in RETYPE_FILTER):
            continue
        f = fm.getFunctionAt(get_addr(m["Address"]))
        if f is not None:
            wanted[f.getEntryPoint()] = f
    if not wanted:
        print("locals: RETYPE_FILTER matched no method that is its own function start.")
        return

    ifc = DecompInterface()
    ifc.setOptions(DecompileOptions())
    ifc.openProgram(currentProgram)
    retyped = 0
    touched = 0
    ambiguous = 0
    failed = 0
    unreadable = []
    monitor.initialize(len(wanted))
    monitor.setMessage("Locals")
    try:
        for f in wanted.values():
            monitor.incrementProgress(1)
            if monitor.isCancelled():
                break
            try:
                res = ifc.decompileFunction(f, DECOMPILE_TIMEOUT, monitor)
            except Exception as e:
                unreadable.append((f.getEntryPoint(), "%s" % e))
                continue
            hf = None if res is None else res.getHighFunction()
            if hf is None:
                unreadable.append((f.getEntryPoint(),
                                   "" if res is None else res.getErrorMessage()))
                continue
            before = retyped
            for sym, choices in local_votes(hf).values():
                if len(choices) != 1:
                    ambiguous += 1
                    continue
                dt = list(choices.values())[0]
                try:
                    HighFunctionDBUtil.updateDBVariable(
                        sym, sym.getName(), dt, USER_DEFINED
                    )
                    retyped += 1
                except Exception:
                    failed += 1
            if retyped > before:
                touched += 1
    finally:
        ifc.dispose()

    print("locals: %d retyped across %d of %d function(s)"
          % (retyped, touched, len(wanted)))
    # Never silently. The skipped ones are exactly the locals still worth a look by hand,
    # so a count of zero and a count of forty mean very different things for the reader.
    if ambiguous:
        print("  %d left alone: one slot, two classes - Split Out As New Variable"
              % ambiguous)
    if failed:
        print("  %d refused by the type manager" % failed)
    if unreadable:
        print("  %d would not decompile, first 5:" % len(unreadable))
        for addr, why in unreadable[:5]:
            print("    %s %s" % (addr, why))


def mark_noreturn(starts, label):
    marked = 0
    for target, count in find_noreturn(starts):
        if count < MIN_NORET_HITS:
            break
        if accept_helper(target, "precedes %d function starts" % count):
            marked += 1
        elif target not in known_noreturn:
            # Usually means the target has no decodable bytes: some builds ship the
            # start of .text encrypted and the throw helpers sit inside it. The
            # bounds step still fixes those, because it states them outright.
            print("  could not mark %s noreturn (precedes %d function starts)"
                  % (target, count))
    marked += find_thunk_helpers()
    print("noreturn %s: %d helper(s) marked" % (label, marked))
    return marked


# --- step 1: types ------------------------------------------------------------

if PARSE_HEADER:
    header = askFile("il2cpp.h from Il2CppDumper", "Open")
    flat = sanitise_header(header.absolutePath)
    print("Parsing il2cpp.h (slow, be patient)...")
    try:
        CParser(dtm, True, None).parse(ByteArrayInputStream(flat.encode("utf-8")))
        print("Types parsed.")
        types_ok = True
    except Exception as e:
        types_ok = False
        print("Header parse failed: %s" % e)
        dumped = dump_header(header.absolutePath, flat)
        print("Wrote the flattened header to %s so the reported line" % dumped)
        print("number means something. Fix the type, or parse that file by hand")
        print("via File > Parse C Source, then re-run with PARSE_HEADER = False.")

f = askFile("script.json from Il2CppDumper", "Open")
with open(f.absolutePath, "rb") as fh:
    data = json.loads(fh.read().decode("utf-8"))

methods = data.get("ScriptMethod", [])

TRUST_ENDS = bool(data.get("Addresses"))
starts = set(get_addr(a) for a in data.get("Addresses", []))
# Addresses is meant to cover every method, but a method missing from it would
# otherwise get no function created at all.
starts |= set(get_addr(m["Address"]) for m in methods)
starts = sorted(starts, key=lambda a: a.getOffset())
metadata_offsets.update(a.getOffset() for a in starts)
method_offsets.update(get_addr(m["Address"]).getOffset() for m in methods)
print("%d function starts from metadata, %d of them named methods"
      % (len(starts), len(method_offsets)))
print("Ends are stated for the methods and clamped for the rest.")
if not TRUST_ENDS:
    print("script.json has no Addresses array (older Il2CppDumper). Function ends")
    print("will be clamped to the next method rather than stated - a method with")
    print("non-method code after it keeps whatever the analyzer chose.")

# --- step 2 and 3: noreturn helpers, then function bounds ---------------------
#
# Ghidra infers where a function ends from control flow. script.json already
# states it, so the inference is not needed and can only be wrong.
#
# The two steps alternate: fixing the bounds promotes a throw helper buried inside
# a merged function to a function of its own, which is what makes it markable on
# the next round. Runs to a fixpoint rather than a fixed number of passes.

# First, before a single noreturn flag is set: the analyzer this turns off is what
# acts on those flags, and it takes the method's exception blocks with it.
disable_body_fixup()

monitor.initialize(len(starts))
monitor.setMessage("Finding noreturn helpers")
unmark_methods()
unmark_thin_helpers()
mark_noreturn(scan_set(starts), "round 1")
fix_bounds(starts, "pass 1")

# After the first bounds pass, so the gaps searched are the real ones rather than
# the ones a merged function was hiding; before the later rounds, so what it creates
# can still be marked noreturn and, in step 6, named.
create_missing_callees()

for round_no in range(2, NORET_ROUNDS + 1):
    if not mark_noreturn(scan_set(starts), "round %d" % round_no):
        break
    fix_bounds(starts, "pass %d" % round_no)

# --- step 4: names, strings, metadata ----------------------------------------

monitor.initialize(len(methods))
monitor.setMessage("Methods")
for m in methods:
    set_name(get_addr(m["Address"]), m["Name"])
    monitor.incrementProgress(1)

strings = data.get("ScriptString", [])
monitor.initialize(len(strings))
monitor.setMessage("Strings")
for i, s in enumerate(strings, start=1):
    addr = get_addr(s["Address"])
    createLabel(addr, "StringLiteral_%d" % i, True, USER_DEFINED)
    setEOLComment(addr, s["Value"])
    monitor.incrementProgress(1)

metas = data.get("ScriptMetadata", [])
monitor.initialize(len(metas))
monitor.setMessage("Metadata")
for meta in metas:
    addr = get_addr(meta["Address"])
    set_name(addr, meta["Name"])
    setEOLComment(addr, meta["Name"])
    if meta.get("Signature"):
        set_type(addr, meta["Signature"])
    monitor.incrementProgress(1)

meta_methods = data.get("ScriptMetadataMethod", [])
monitor.initialize(len(meta_methods))
monitor.setMessage("Metadata methods")
for meta in meta_methods:
    addr = get_addr(meta["Address"])
    # Every one of these names ends in "()", so a read of the slot prints as
    # `lVar3 = Method$List<int>.Add();` - a call that is not there. The comment keeps
    # the name whole; the label is what the decompiler puts in the expression.
    set_name(addr, meta["Name"][:-2] if meta["Name"].endswith("()") else meta["Name"])
    setEOLComment(addr, meta["Name"])
    monitor.incrementProgress(1)

# --- step 5: signatures ------------------------------------------------------

if not APPLY_SIGNATURES:
    print("Skipping signatures: APPLY_SIGNATURES is False.")
elif not types_ok:
    print("Skipping signatures: without types every parse would fail.")
else:
    if SIG_FILTER:
        wanted = [m for m in methods if any(k in m["Name"] for k in SIG_FILTER)]
        print("SIG_FILTER is set: signing %d of %d methods. The rest stay untyped."
              % (len(wanted), len(methods)))
    else:
        wanted = methods
        print("Applying %d signatures (all of them - this takes hours)" % len(wanted))

    monitor.initialize(len(wanted))
    monitor.setMessage("Signatures")
    for m in wanted:
        set_sig(get_addr(m["Address"]), m["Signature"][:-1], m["Name"])
        monitor.incrementProgress(1)

    if sig_failures:
        print("signatures: %d of %d failed, first 10:"
              % (len(sig_failures), len(wanted)))
        for addr, sig, why in sig_failures[:10]:
            print("  %s %s -- %s" % (addr, sig, why))
    else:
        print("signatures: all %d applied." % len(wanted))

# --- step 6: readable names for the runtime's helpers ------------------------
#
# Last of the mutating steps, so the candidate set is the final function list
# rather than an intermediate one. Nothing after this depends on it: it only
# renames.

# First, so the chain walk below sees them as named and does not try to explain two
# functions that raise nothing in terms of an exception class further down their trees.
print("codegen helpers:")
helpers = name_codegen_helpers()
print("  type slots: %d named from their constructor"
      % name_type_slots(helpers.get("il2cpp_codegen_object_new")))
name_runtime_helpers()

# --- step 7: re-assert the stated bounds -------------------------------------
#
# Last, because every noreturn flag set above shrinks the bodies of the functions
# that call it. Anything earlier gets undone by what follows it - including by
# Ghidra's own analysis, which is why the pass starts by waiting for it.

assert_stated_bounds(starts)

# After the widening, so the walk is confined to a body that spans the whole method,
# and after the last noreturn round, so refused_methods holds every method the shape
# rule offered up rather than only those seen by round 1. The IDA script runs these
# two the other way round because set_func_end will not grow a function over bytes
# that are not code yet, so there the decoding has to come first.
repair_queued_flow()

# Again, because a body can lose its holes and still end early: repair_queued_flow
# decodes the code, this puts it inside the function. Cheap - it only compares extents
# - and reports 0 when the first call was enough.
assert_stated_bounds(starts)

# --- step 8: locals ----------------------------------------------------------
#
# Last of the passes that change what the decompiler prints, and deliberately after the
# bounds work: a function whose body still ends early decompiles to a different set of
# locals, so voting on them before step 7 would type variables that stop existing.

retype_locals()

# --- step 9: self-check ------------------------------------------------------
#
# The failure this guards against is silent: decompiling gives you a different
# method's body and the only clue is the title. So assert the invariant that
# makes the decompiler trustworthy - every named method is its own function
# start, not a fragment absorbed into a neighbour.

bad = []
for m in methods:
    addr = get_addr(m["Address"])
    if fm.getFunctionAt(addr) is None:
        owner = fm.getFunctionContaining(addr)
        bad.append((addr, m["Name"], None if owner is None else owner.getEntryPoint()))

if bad:
    print("WARNING: %d method(s) are not their own function start." % len(bad))
    print("Decompiling these gives you the WRONG function. First 10:")
    for addr, name, owner in bad[:10]:
        where = "no function" if owner is None else "inside %s" % owner
        print("  %s %s (%s)" % (addr, name, where))
    print("Re-run the script with PARSE_HEADER = False to resolve.")
else:
    print("Self-check passed: every method is its own function start.")

print("Script finished!")
