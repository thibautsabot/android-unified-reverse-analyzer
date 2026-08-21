# -*- coding: utf-8 -*-
#
# IL2CPPDumper -> IDA, in one pass. Derived from ida_with_struct_py3.py in
# Il2CppDumper (Perfare, MIT), with five defects fixed:
#
#   1. noreturn runtime helpers are detected and marked. IL2CPP functions often
#      END on a `bl` to a throw/rethrow helper with no following `ret`. IDA
#      assumes a `bl` returns, walks into the next function, and merges the two
#      - so F5 silently decompiles the wrong function.
#   2. function bounds come from script.json's Addresses, and any auto-analysis
#      function that swallowed a real start is dissolved first. Upstream only
#      deleted a conflict that began at exactly the same address, so overlaps
#      failed - silently, because add_func's return value was ignored. Two
#      consecutive entries bound a function only where the metadata names the
#      method, though; over the runtime's C++ the list is sparse, so there the
#      ends are clamped and IDA's own functions are left alone (see method_starts).
#   3. il2cpp.h is sanitised before parsing: it declares a field named `catch`,
#      which is legal in C# but a keyword to IDA's C++ parser.
#   4. the throw helpers themselves may live in an encrypted region. Some builds
#      ship the first tens of KB of .text as high-entropy noise that the runtime
#      decrypts on load, and the helpers sit inside it. IDA cannot decode those
#      bytes, so it never makes a function there, so there is nothing to mark
#      noreturn - and fix 1 silently does nothing. Handled two ways: a trap
#      instruction is patched into the DATABASE (never the file) so the hbtelper
#      becomes markable, and function ends are stated outright from the metadata
#      rather than left to flow analysis.
#   5. helpers that fix 1 structurally cannot see are found by a second rule.
#      Fix 1 notices a helper because a function starts right after a call to it;
#      a helper called only from the MIDDLE of methods has no function start
#      after any of its call sites, so that rule is blind to it forever. Such a
#      helper is instead recognised by its own shape: a handful of instructions,
#      no `ret` anywhere, ending on a call. The scan also covers the functions
#      IDA found in .text, not just the methods in the metadata, and the rounds
#      run to a fixpoint - marking one helper un-merges the function that hid
#      the next. Two things make that fixpoint real rather than a fixed count:
#      a helper keeps its flag when the bounds pass rebuilds its function, and a
#      round counts only helpers it had not seen before. Without either, the same
#      helper is rediscovered every round and the rounds never converge.
#
# And one addition that fixes no defect but decides whether the output can be
# read at all: the runtime's shared helpers are named by what they DO. A correct
# decompilation whose body is `sub_278C014()` six times still tells you nothing.
# So each small, heavily-called helper has its call chain followed a few hops,
# and any exception class named along the way becomes its name - the runtime
# raises by class name, as a plain C string, so the answer is always in there.
# `il2cpp_throw_NullReferenceException()` at a call site is the same code, but
# now readable as what the C# said: a null check the compiler inserted, not one
# anybody wrote. See name_runtime_helpers.
#
# Tested with IDA 9.3 (IDAPython, Python 3) and Il2CppDumper v6.7.46 on an arm64
# libil2cpp.so. Written to survive a different game: calls and returns are
# identified by asking the processor module rather than by matching mnemonics, the
# breakpoint used by fix 4 has an encoding per architecture, and a script.json with
# no Addresses array downgrades function ends from stated to clamped instead of
# quietly absorbing whatever sits between two methods. What is NOT handled is a
# binary whose code is packed or virtualised rather than merely part-encrypted -
# there the premise fails, that metadata addresses point at real instructions.
#
# Every count is printed. A step that fixed nothing says so.
#
# The script is idempotent: if the self-check at the end reports failures, run it
# again. A helper can only be marked noreturn once it is its own function, so a
# helper buried inside a merged function is reachable on the second pass.

import json
import re

import ida_auto
import ida_bytes
import ida_funcs
import ida_hexrays
import ida_ua
import idaapi
import idautils
import idc

# A genuine throw helper sits before hundreds of function starts. A coincidental
# call before one start is not evidence of anything.
MIN_NORET_HITS = 8

# Set False to re-run only the cheap parts - noreturn detection, function bounds,
# names and the self-check - on a database that already has types applied. Skips
# the il2cpp.h parse and the ~265k apply_type calls, so it finishes in seconds.
APPLY_TYPES = True

# Which functions get their locals retyped from the types applied above. Empty means
# none, and that is deliberate rather than a default worth changing: unlike every other
# pass here, this one has to decompile each function to see its locals, so all 265180
# methods would take days. Point it at the class you are about to read.
#
#   RETYPE_FILTER = ["GenLevelController"]
#
# Hex-Rays already propagates most callee parameter types back onto the caller's
# variables, so this buys far less here than the same pass buys in Ghidra. It is kept so
# the two scripts stay one tool described twice, and it still catches the cases Hex-Rays
# leaves as a cast at the call site rather than a retype of the variable.
RETYPE_FILTER = []

# Instantiations that every other instantiation shares. IL2CPP compiles one body for all
# reference-type arguments, so `List<T>_o *` or `List<object>_o *` on a parameter is the
# ABSENCE of evidence about the caller, not evidence. Writing one onto a variable is how
# `_items` becomes an object[] and an inlined List<int>.Add degrades from
# `m_Items[i] = v` to raw pointer arithmetic - a step backwards from the untyped variable.
GENERIC_SHARED = ("_T__o", "_T1__o", "_T2__o", "_T3__o", "_object__o")

# Variable types carrying so little that a class pointer is strictly better. A variable
# already typed to a concrete class is never touched - it was either inferred correctly or
# set by hand with Y, and clobbering a hand retype would undo the reader's work on every
# re-run.
VAGUE_TYPES = ("Il2CppObject", "Il2CppClass", "void", "__int64", "unsigned __int64",
               "_QWORD", "_UNKNOWN")

# Only consulted if the processor module cannot answer (see is_call/is_return).
# Deliberately incomplete for ARM32, where a return is `pop {..,pc}` or `bx lr`
# and matching it by mnemonic alone would need the operands too: an unrecognised
# return makes a function look like it never returns, so the safe direction is to
# treat an unknown instruction as a possible return and find fewer helpers.
CALL_MNEMS = ("BL", "BLX", "CALL")
RET_MNEMS = ("RET", "RETN", "BX", "POP", "LDM", "LDMFD", "LEAVE")

# A throw thunk is tiny: save the link register, call the real throw, done. Two
# guards keep an ordinary function out of the net - a body longer than this, or
# too few callers to be a shared helper.
# How many owners to detach a shared tail chunk from before giving up. One shared
# implementation can be the tail of every stub that branches into it, so this is a
# runaway guard rather than a real ceiling.
MAX_TAIL_OWNERS = 512

MAX_HELPER_INSNS = 8
MIN_HELPER_XREFS = 8

# Marking one helper lets analysis split the functions that swallowed the next
# one, so each round can expose more. Stops early once a round finds nothing.
NORET_ROUNDS = 4

# How many hops to follow a helper's outgoing calls looking for something that
# names it. The throw thunks reach their exception class in three; much past six
# and the chain has left the helper family and is describing unrelated code.
NAME_CHAIN_DEPTH = 6

# Instructions to decode per hop. These are thunks and small raisers - a body
# longer than this means the walk has wandered into a real function.
NAME_CHAIN_INSNS = 24

# Bounds a string read, and rejects the binary noise a mis-resolved pointer gives.
MAX_STRING_LEN = 96

# The two helpers the chain walk can never name, because neither of them raises
# anything: the metadata initialiser and the object allocator. They are identified by
# what happens around their call sites instead - see name_codegen_helpers - and from a
# sample, because telling one function from another takes a majority, not a census.
ROLE_SAMPLE = 400
ROLE_SITE_INSNS = 8
MIN_ROLE_SITES = 8

# What the runtime passes to its exception raisers: the namespace and the class,
# as two plain C strings, looked up by name at throw time. The class name is the
# whole prize - it is what turns sub_278C014() into a sentence.
EXCEPTION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*Exception$")

# Reaching one of these names a helper even when no exception class turned up on
# the way. Keyed by the imported symbol the chain ends at.
CHAIN_TERMINALS = {
    "__cxa_throw": "il2cpp_throw",
    "__cxa_rethrow": "il2cpp_rethrow",
    "__cxa_bad_cast": "il2cpp_throw_bad_cast",
    "__stack_chk_fail": "il2cpp_stack_check_failed",
    "abort": "il2cpp_abort",
    "longjmp": "il2cpp_longjmp",
}

# Name prefixes this script is free to overwrite. Everything else came from the
# metadata and is authoritative. `loc_` matters as much as `sub_`: a bare `loc_`
# at a call site is IDA saying there is no function there at all.
GENERIC_PREFIXES = (
    "sub_", "loc_", "nullsub_", "j_", "unknown_libname_", "il2cpp_noreturn_",
)

# Set False to refuse to patch. A throw helper in an encrypted region has no
# decodable bytes, so IDA will not make it a function and it cannot be marked
# noreturn. Patching a trap instruction over its first bytes makes it markable.
# This edits the IDA database only - the .so on disk is never touched - and the
# region is unreadable noise either way, so nothing analysable is lost.
PATCH_UNDECODABLE = True

# C++ keywords that are legal C# identifiers, so il2cpp.h can emit them as field
# names. Must never list a keyword the generator uses as a TYPE - `bool` appears
# 5000+ times as a type in this header and renaming it would destroy the parse.
CXX_KEYWORD_FIELDS = [
    "catch", "class", "delete", "explicit", "export", "friend", "mutable",
    "namespace", "new", "operator", "private", "protected", "public",
    "template", "this", "throw", "try", "typename", "using", "virtual",
]

# The same problem in script.json's signatures: a parameter can be named `catch`
# or `static`, because IL permits any name even where C# would not. Type keywords
# are deliberately absent - only the parameter-name position is rewritten, and a
# signature with an unnamed parameter would put a type keyword in that position.
SIG_KEYWORDS = set(CXX_KEYWORD_FIELDS) | set([
    "auto", "break", "case", "continue", "default", "do", "else", "extern",
    "for", "goto", "if", "inline", "register", "return", "sizeof", "static",
    "switch", "typedef", "while",
])
SIG_PARAM_NAME = re.compile(r"(?<=[\s*])([A-Za-z_][A-Za-z0-9_]*)(?=\s*[,)])")

imageBase = idaapi.get_imagebase()

# Every helper marked so far, from any source. Propagation walks back from these.
known_noreturn = set()

# Named methods a rule offered up as helpers and accept_helper turned away. Only so
# each is reported once - the same method is reachable from several rules.
refused_methods = set()

# Addresses whose FUNC_NORET a repair pass cleared, waiting for their callers to be
# re-analysed. Queued rather than repaired on the spot so the re-analysis runs against
# corrected function bounds instead of the truncated ones the stale flag produced.
flow_repair_queue = []

# Filled in once script.json is read. A metadata start is the only independent
# evidence available for where a function ends: the next one begins there.
metadata_starts = set()

# The subset of those that are named C# methods. The distinction decides whether
# this script is allowed to overrule IDA, and it matters more than it looks:
#
#   Addresses lists every entry point the dumper knows, including the IL2CPP
#   runtime's own C++ in .text - but it lists them SPARSELY there. In the game
#   this was written against, 11 consecutive pairs span 61% of .text, one of them
#   a megabyte, and not one .text entry is a method. Treating such a pair as one
#   function's extent is how a whole megabyte of real functions gets deleted and
#   replaced by a single bogus one; every call into it then decompiles as a bare
#   `loc_` with no arguments.
#
#   A ScriptMethod start is different: the dumper knows the method's identity, so
#   the space up to the next start really does belong to it, and a function IDA
#   invented inside it is a guess to be discarded. Ends are therefore STATED for
#   methods and merely CLAMPED for everything else.
method_starts = set()

# False when script.json carries no Addresses array - older Il2CppDumper builds
# omit it. Then the only ends available are the next METHOD start, which is not
# the same thing: anything between two methods would get absorbed. So the ends
# are treated as a ceiling to clamp to rather than a boundary to state.
TRUST_ENDS = True


def get_addr(addr):
    return imageBase + addr


def set_name(addr, name):
    if idc.set_name(addr, name, idc.SN_NOWARN | idc.SN_NOCHECK) == 0:
        # Taken already - several distinct thunks raise the same exception class.
        # Hex, so the suffix matches the address shown everywhere else.
        idc.set_name(addr, "%s_%X" % (name, addr), idc.SN_NOWARN | idc.SN_NOCHECK)


def load_header(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    for kw in CXX_KEYWORD_FIELDS:
        src, n = re.subn(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % kw, kw + "_", src)
        if n:
            print("header: renamed %d bare `%s` -> `%s_`" % (n, kw, kw))
    return src


def sanitise_signature(sig):
    """Rename any parameter called after a C keyword. Only a token sitting where a
    parameter name goes - after a space or a star, before a comma or the closing
    paren - is touched, so type names are never rewritten."""
    return SIG_PARAM_NAME.sub(
        lambda m: m.group(1) + "_" if m.group(1) in SIG_KEYWORDS else m.group(1),
        sig,
    )


def find_noreturn(starts):
    """A function start preceded by a call implies that call's target never
    returns - otherwise the compiler would have emitted a `ret` there."""
    hits = {}
    for ea in starts:
        prev = idc.prev_head(ea)
        if prev == idaapi.BADADDR:
            continue
        if not is_call(prev):
            continue
        target = idc.get_operand_value(prev, 0)
        if target > 0:
            hits[target] = hits.get(target, 0) + 1
    return sorted(
        ((t, c) for t, c in hits.items() if c >= MIN_NORET_HITS),
        key=lambda kv: -kv[1],
    )


def scan_set(starts):
    """The addresses the adjacency rule looks at. Metadata covers the generated
    methods only; the IL2CPP runtime's own C++ sits in .text and auto-analysis
    found those functions itself. A helper called from there is invisible to a
    scan over method starts alone."""
    return sorted(set(starts) | set(idautils.Functions()))


def tail_call_without_return(f):
    """What f's last instruction calls, if f is a short function that ends on a
    call and contains no return at all - otherwise None. Such a function cannot
    return: control reaches the call and there is nothing after it, so the callee
    must be a throw. A compiler would have emitted a `ret` otherwise.

    Requiring the last instruction to be a CALL and not a branch is what keeps
    ordinary tail-call thunks - which do return, through the callee - out."""
    last = None
    n = 0
    for ea in idautils.Heads(f.start_ea, f.end_ea):
        if not idc.is_code(ida_bytes.get_full_flags(ea)):
            return None
        n += 1
        if n > MAX_HELPER_INSNS:
            return None
        if is_return(ea):
            return None
        last = ea
    if n == 0 or not is_call(last):
        return None
    target = idc.get_operand_value(last, 0)
    return target if target > 0 else None


def boundary_is_known(f):
    """Is f's end a boundary we have independent evidence for?

    "No return anywhere in the body" only means something if the body is really
    over. If IDA merely cut the function short at a call, the `ret` is sitting
    just past the end and the shape rule would read it as a throw. The metadata
    stating a method starts there, or another function already starting there, is
    that evidence."""
    return f.end_ea in metadata_starts or ida_funcs.get_func(f.end_ea) is not None


def has_enough_callers(ea):
    """A shared throw helper is called from everywhere. Requiring that stops a
    function IDA merely mis-bounded - cut short at a call, so it looks like a
    thunk - from being declared noreturn on the strength of one call site."""
    n = 0
    for _ in idautils.CodeRefsTo(ea, 0):
        n += 1
        if n >= MIN_HELPER_XREFS:
            return True
    return False


def arch():
    """(processor name, 64-bit?), across the IDAPython versions that moved it."""
    try:
        import ida_ida
        return ida_ida.inf_get_procname(), ida_ida.inf_is_64bit()
    except Exception:
        inf = idaapi.get_inf_structure()
        return inf.procname, inf.is_64bit()


PROCNAME, IS64 = arch()


def patch_trap(ea):
    """Overwrite ea with a breakpoint - an instruction with no fall-through, so a
    function can be made there and marked noreturn. Database only.

    IL2CPP ships arm64, armv7 and x86_64, and IDA cannot tell you the encoding,
    so it is a table. Thumb is checked per address because a Thumb breakpoint is
    two bytes and the T register is what says which mode the code is in."""
    if PROCNAME == "ARM":
        if not IS64 and idc.get_sreg(ea, "T") == 1:
            ida_bytes.patch_word(ea, 0xBE00)  # BKPT, Thumb
            return True
        ida_bytes.patch_dword(ea, 0xD4200000 if IS64 else 0xE1200070)  # BRK / BKPT
        return True
    if PROCNAME.startswith("metapc"):
        ida_bytes.patch_byte(ea, 0xCC)  # INT3
        return True
    return False


def insn_at(ea):
    insn = ida_ua.insn_t()
    return insn if ida_ua.decode_insn(insn, ea) else None


def is_call(ea):
    """Does ea call, leaving a return address? Read off the processor module's own
    instruction feature bits, so this needs no per-architecture mnemonic list."""
    insn = insn_at(ea)
    if insn is None:
        return idc.print_insn_mnem(ea).upper() in CALL_MNEMS
    try:
        import ida_idp
        return bool(insn.get_canon_feature() & ida_idp.CF_CALL)
    except Exception:
        return idc.print_insn_mnem(ea).upper() in CALL_MNEMS


def is_return(ea):
    """Does ea return from its function? Asked of the processor module, which
    knows every form - AArch64's `ret`, ARM's `pop {..,pc}`, x86's `retn`."""
    insn = insn_at(ea)
    if insn is not None:
        try:
            import ida_idp
            return bool(ida_idp.is_ret_insn(insn))
        except Exception:
            pass
        try:
            return bool(idaapi.is_ret_insn(ea))
        except Exception:
            pass
    return idc.print_insn_mnem(ea).upper() in RET_MNEMS


def make_code(ea):
    """Define ea as an instruction. Auto-analysis leaves a method undefined when
    nothing references it - IL2CPP reaches most methods through data tables it
    does not follow - and add_func refuses to work on undefined bytes."""
    if ida_ua.create_insn(ea):
        return True
    # Something else claims these bytes: a data item invented by auto-analysis,
    # or a stale definition. del_items drops whatever item contains ea.
    ida_bytes.del_items(ea, ida_bytes.DELIT_SIMPLE, 4)
    return ida_ua.create_insn(ea) != 0


def force_function(ea):
    """Make ea a function, even if the bytes there do not decode."""
    ida_bytes.del_items(ea, ida_bytes.DELIT_SIMPLE, 4)
    if ida_ua.create_insn(ea) == 0:
        if not PATCH_UNDECODABLE or not patch_trap(ea):
            print("  %s does not decode and cannot be trapped on %s - NOT marked"
                  % (hex(ea), PROCNAME))
            return False
        ida_bytes.del_items(ea, ida_bytes.DELIT_SIMPLE, 4)
        if ida_ua.create_insn(ea) == 0:
            return False
        print("  %s does not decode - patched a trap into the database" % hex(ea))
    ida_funcs.add_func(ea, ea + 4)
    return ida_funcs.get_func(ea) is not None


def detach_tail(ea):
    """Detach ea from the function that claims it as a tail chunk.

    A tail is a range of code owned by a function that starts somewhere else, so
    get_func here reports that *owner* rather than nothing, and add_func refuses
    the address because it is already spoken for. Flow analysis produces these
    the same way it produces merged functions: by following a call that never
    returns, or a branch into code that belongs to another method.

    A chunk can have MORE THAN ONE owner, and remove_func_tail detaches a single
    one. IL2CPP emits a tiny stub per generic instantiation that tail-branches into
    one shared implementation - hundreds of `SetStateMachine` stubs are three
    instructions ending in `b 0x4c59c78` - and IDA registers that target as a tail
    of every stub reaching it. So detaching once leaves the chunk owned by the rest,
    add_func still refuses the address, and the next run reports the same method
    failing under a DIFFERENT owner's name. Hence the loop: detach until the address
    is free, not until one call succeeds."""
    detached = 0
    for _attempt in range(MAX_TAIL_OWNERS):
        chunk = ida_funcs.get_fchunk(ea)
        if chunk is None or not (chunk.flags & idaapi.FUNC_TAIL):
            break
        owner = ida_funcs.get_func(chunk.owner)
        if owner is None:
            break
        if not ida_funcs.remove_func_tail(owner, ea):
            break
        detached += 1
    return detached > 0


def clear_range(start, end):
    """Delete every function beginning strictly inside (start, end). add_func
    refuses a range that overlaps an existing function, and a function starting
    mid-method is an auto-analysis guess - the metadata says the method starts at
    `start`, so the guess is what has to go."""
    n = 0
    f = ida_funcs.get_next_func(start)
    while f is not None and f.start_ea < end:
        nxt = ida_funcs.get_next_func(f.start_ea)
        if ida_funcs.del_func(f.start_ea):
            n += 1
        f = nxt
    return n


def split_container(start):
    """Cut back a function that began before `start` and runs past it.

    clear_range cannot help here: it deletes functions that *begin* inside the
    range, and this one begins outside it. The metadata says a method starts at
    `start`, so what is wrong is the container's end - trim it and the address is
    free, with the earlier function still intact. If IDA refuses the new end the
    container is dissolved instead, which is what the pass does to merged
    functions anyway."""
    f = ida_funcs.get_func(start)
    if f is None or f.start_ea >= start:
        return False
    if ida_funcs.set_func_end(f.start_ea, start):
        return True
    return bool(ida_funcs.del_func(f.start_ea))


def create_function(start, end=None):
    """Create a function at start, escalating through the reasons add_func can
    refuse. With `end` the bounds are stated; without it flow analysis picks them.
    Returns the step that worked, or None."""
    def add():
        return (ida_funcs.add_func(start) if end is None
                else ida_funcs.add_func(start, end))

    if add():
        return "clean"
    if detach_tail(start) and add():
        return "tail detached"
    if make_code(start) and add():
        return "undefined first"
    if end is not None and clear_range(start, end) and add():
        return "overlap cleared"
    if end is not None and split_container(start) and add():
        return "container split"
    # Last resort: let flow analysis choose the bounds. Wrong bounds still beat no
    # function at all, and the caller clamps the end afterwards anyway.
    if end is not None and ida_funcs.add_func(start):
        return "flow-inferred"
    return None


def unmark_methods():
    """Clear FUNC_NORET from every named method carrying it.

    The guard in accept_helper stops the script creating new false positives, but a
    flag already written to the database outlives the run that wrote it - and while
    it is there, every CALLER of that method still decompiles truncated. Re-running
    a fixed script over a database it has already damaged has to undo the damage;
    otherwise the only cure is rebuilding from the .so, which costs hours.

    Clearing the flag alone does not undo it. Believing a call never returns, IDA
    deletes the fall-through edge from the CALLING instruction and stops decoding
    there, so the bytes after that call were never turned into code. That edge belongs
    to the caller, not to the flag, so the caller still decompiles as
    `Dispose(...); return;` with the rest of the method missing -
    reanalyze_callers is what puts the edge back.

    Measured on GenLevelController$ShuffleItemVisual: the method continues at
    0x2aebd4c and NOTHING branches there - it is reached only by falling through the
    `cbnz` after a call to List.Enumerator<T>::Dispose. That one stale edge hid two
    thirds of the method, including the shuffle the method exists to do."""
    repaired = 0
    for ea in sorted(method_starts):
        f = ida_funcs.get_func(ea)
        if f is None or f.start_ea != ea or not (f.flags & idaapi.FUNC_NORET):
            continue
        f.flags &= ~idaapi.FUNC_NORET
        if ida_funcs.update_func(f):
            repaired += 1
            flow_repair_queue.append(ea)
    if repaired:
        print("repaired %d method(s) an earlier run wrongly flagged noreturn"
              % repaired)
    return repaired


def redecode_holes(starts):
    """Decode code missing from inside a function, whatever stopped the decoder.

    A method's end comes from the metadata, so unexplored bytes INSIDE one mean the
    decoder gave up part way through - and the instruction before the hole says why.
    Where that instruction is a call, its fall-through was deleted because the callee
    was believed never to return.

    This looks for the symptom rather than the flag on purpose. An earlier run may have
    cleared the flag already, and clearing it neither restores the deleted edge nor
    decodes the bytes, so a repair driven only by what is still flagged would never
    fire again and the truncation would survive every future run. It also catches
    truncation this script had no part in - IDA reaches the same wrong conclusion about
    IL2CPP throw helpers on its own."""
    repaired = 0
    for i in range(len(starts) - 1):
        ea, end = starts[i], starts[i + 1]
        if ea not in method_starts:
            continue
        hole = ida_bytes.next_unknown(ea, end)
        if hole >= end:
            continue
        prev = ida_bytes.prev_head(hole, ea)
        if prev == idaapi.BADADDR or not is_call(prev):
            continue
        ida_auto.plan_range(hole, end)
        repaired += 1
    if repaired:
        ida_auto.auto_wait()
    return repaired


def repair_queued_flow(starts):
    """Put back the flow a noreturn flag cut, from both directions.

    reanalyze_callers is the one API that re-derives a deleted fall-through - it is
    what IDA itself runs when a function's noreturn status changes.

    Three sources, because the damage outlives its own evidence. The queue is what this
    run un-flagged. refused_methods is what the shape rule offered up and accept_helper
    turned away - a set that matters because a run that already cleared the flags left
    the truncated callers behind, and the rule still names the same methods even though
    nothing is flagged any more. redecode_holes then covers whatever is still sitting
    as unexplored bytes."""
    targets = set(flow_repair_queue) | refused_methods
    for ea in targets:
        ida_auto.reanalyze_callers(ea, 0)
    if targets:
        ida_auto.auto_wait()
    holes = redecode_holes(starts)
    if targets or holes:
        print("flow repair: callers of %d function(s) re-analysed, %d body(ies) "
              "redecoded" % (len(targets), holes))
    return len(targets) + holes


def unmark_thin_helpers():
    """Undo this script's own marks on functions that no longer qualify as helpers.

    A function carrying one of this script's names and fewer than MIN_HELPER_XREFS
    callers was marked before that rule required a caller count: a method's cold
    null-check block rather than a shared thunk. The flag and the name both outlive
    the run that wrote them, so the fix has to undo them.

    The name is the only evidence available for who set the flag - IDA records that
    a function is noreturn, not why - which is exactly why this script names
    everything it marks. Clearing the name back to the default also makes the
    function eligible again, so one that does qualify is re-marked on this run."""
    repaired = 0
    for ea in list(idautils.Functions()):
        if ea in method_starts:
            continue
        name = idc.get_name(ea) or ""
        if not name.startswith(("il2cpp_throw", "il2cpp_noreturn_")):
            continue
        if has_enough_callers(ea):
            continue
        f = ida_funcs.get_func(ea)
        if f is None or f.start_ea != ea:
            continue
        if f.flags & idaapi.FUNC_NORET:
            f.flags &= ~idaapi.FUNC_NORET
            ida_funcs.update_func(f)
            # See unmark_methods: the deleted fall-through lives on the caller.
            flow_repair_queue.append(ea)
        # Back to sub_XXXX, so a later round can reconsider it on the current rules.
        idc.set_name(ea, "", idc.SN_NOWARN | idc.SN_NOCHECK)
        repaired += 1
    if repaired:
        print("repaired %d function(s) an earlier run marked as helpers on fewer"
              % repaired)
        print("than %d callers - cold blocks, not shared thunks" % MIN_HELPER_XREFS)
    return repaired


def already_noreturn(ea):
    f = ida_funcs.get_func(ea)
    return f is not None and f.start_ea == ea and bool(f.flags & idaapi.FUNC_NORET)


def set_noreturn(ea):
    """Ensure a function exists at ea and carries FUNC_NORET. True if the flag is
    set once this returns, whether or not this call is what set it.

    That distinction is the whole point. Reporting an already-correct helper as a
    failure is how a re-run over a database this script has already processed ends
    up with an EMPTY record of the helpers: every one is skipped, the rounds report
    nothing, and the naming pass loses the set it is seeded from."""
    f = ida_funcs.get_func(ea)
    if f is None or f.start_ea != ea:
        if not force_function(ea):
            print("  could not make %s a function - NOT marked noreturn" % hex(ea))
            return False
        f = ida_funcs.get_func(ea)
    if f is None:
        return False
    if f.flags & idaapi.FUNC_NORET:
        return True
    f.flags |= idaapi.FUNC_NORET
    return bool(ida_funcs.update_func(f))


def accept_helper(ea, why):
    """Record ea as a helper, flag it noreturn, and give it a name that reads as a
    throw at every call site - `sub_278C014` tells you nothing, and these appear
    everywhere.

    Returns True only for a helper this run actually had to flag, so the caller's
    count means "newly discovered" and the rounds can tell progress from
    repetition. A helper that was already flagged is still recorded and still
    counts as known - it just is not news."""
    if ea in known_noreturn:
        return False
    if ea in method_starts:
        # A named C# method is never one of the runtime's anonymous throw thunks.
        # Every throw helper in this binary sits in .text, where not one metadata
        # entry is a method - so a method reaching here is a false positive, and a
        # costly one: the decompiler prunes everything after every call to it, so
        # it is the CALLERS that lose their bodies. That is the exact failure this
        # script exists to prevent, reintroduced from the other side.
        #
        # It happens because fix_bounds cuts a method's end to the next metadata
        # address. Where Addresses has an entry mid-method the body is truncated
        # right after a `bl`, and the shape rule then reads "no ret, ends on a
        # call" - a lie this script manufactured itself. boundary_is_known cannot
        # catch it: the boundary IS a metadata start, which is why it was cut there.
        #
        # Measured on the game this was written against: of 17 methods the shape
        # rule offered up, 16 were ordinary - Component::get_tag, set_tag,
        # TaskAwaiter::get_IsCompleted, four constructors. Exactly one,
        # XmlTextReaderImpl::Throw, really never returns. That is the whole cost.
        if ea not in refused_methods:
            refused_methods.add(ea)
            print("  refused %s (%s): a named method, not a runtime helper"
                  % (hex(ea), why))
        return False
    already = already_noreturn(ea)
    if not set_noreturn(ea):
        return False
    known_noreturn.add(ea)
    # Only if it has nothing better. Step 6 derives real names like
    # il2cpp_throw_NullReferenceException, and a re-run must not undo them.
    if is_generic_name(ea):
        set_name(ea, "il2cpp_noreturn_%X" % ea)
    if already:
        return False
    print("  noreturn: %s (%s)" % (hex(ea), why))
    return True


def find_thunk_helpers():
    """The helpers the adjacency rule is structurally blind to: a thunk called
    only from inside methods, so no function start ever follows a call to it.
    Recognised by its own shape instead of by what surrounds its call sites."""
    found = 0
    # Snapshotted: marking a helper queues reanalysis, which re-bounds functions
    # and so mutates the list being walked.
    for ea in list(idautils.Functions()):
        if ea in known_noreturn:
            continue
        f = ida_funcs.get_func(ea)
        if f is None or f.flags & idaapi.FUNC_NORET:
            continue
        target = tail_call_without_return(f)
        if target is None:
            continue
        # Both rules need the caller count, for the same reason. Once the callee is
        # known noreturn, IDA ends every function right after a call to it - so "no
        # ret in the body" stops being an observation and becomes a consequence of
        # the boundary the flag just moved. Every method's cold null-check block then
        # fits the shape perfectly.
        #
        # Measured on the game this was written against: without the count this rule
        # marked 383 functions in one round and step 6 named 379 of them
        # NullReferenceException - the cold blocks of methods, not shared thunks. The
        # binary settles it: 117301 `bl` reach that helper and not one `b` does, so
        # not a single one of those was the tail call the message claimed.
        #
        # A genuine runtime thunk is called from everywhere, which is what the count
        # tests. A cold block belongs to one method and has one reference. The cost
        # is a shared thunk with fewer than MIN_HELPER_XREFS callers, and that is the
        # safe direction to fail: an unmarked helper leaves its own bounds wrong,
        # while a wrongly marked one truncates every CALLER.
        if not has_enough_callers(ea):
            continue
        if target in known_noreturn:
            why = "ends on a call to %s, %d+ callers" % (hex(target),
                                                         MIN_HELPER_XREFS)
        elif boundary_is_known(f):
            why = "ends on a call with no return, %d+ callers" % MIN_HELPER_XREFS
        else:
            continue
        if accept_helper(ea, why):
            found += 1
    return found


def read_c_string(ea):
    """A NUL-terminated printable string at ea, or None.

    Deliberately not get_strlit_contents: these pointers land in a data blob that
    auto-analysis never typed, so there is no string literal there for IDA to
    return - only bytes that happen to spell one."""
    out = []
    for i in range(MAX_STRING_LEN):
        b = ida_bytes.get_byte(ea + i)
        if b == 0:
            return "".join(out) or None
        if b < 0x20 or b > 0x7E:
            return None
        out.append(chr(b))
    return None


def paired_page_address(ea):
    """The address an adrp/add pair builds, or None.

    A 64-bit address does not fit in one AArch64 instruction, so every string
    pointer arrives as a page load plus an offset. IDA usually leaves a data
    reference to follow - but only where it typed the destination, and the string
    blob these point into is often left as raw bytes with no reference at all.
    Gated on the mnemonics, so on any other architecture it simply never fires."""
    if idc.print_insn_mnem(ea).upper() != "ADD":
        return None
    prev = idc.prev_head(ea)
    if prev == idaapi.BADADDR or idc.print_insn_mnem(prev).upper() != "ADRP":
        return None
    insn, prev_insn = insn_at(ea), insn_at(prev)
    if insn is None or prev_insn is None:
        return None
    # Both operands of the add must be the register the adrp just loaded,
    # otherwise the pair is unrelated and the sum is meaningless.
    reg = prev_insn.ops[0].reg
    if insn.ops[0].reg != reg or insn.ops[1].reg != reg:
        return None
    page = idc.get_operand_value(prev, 1)
    offset = idc.get_operand_value(ea, 2)
    if page <= 0 or offset < 0:
        return None
    return page + offset


def strings_at(ea):
    """Every plausible C string the instruction at ea points at."""
    out = []
    for ref in idautils.DataRefsFrom(ea):
        s = read_c_string(ref)
        if s:
            out.append(s)
    if not out:
        built = paired_page_address(ea)
        if built is not None:
            s = read_c_string(built)
            if s:
                out.append(s)
    return out


def stops_flow(ea):
    """Does control stop advancing past ea? True for a return, an unconditional
    branch, or a trap. Asked of the processor module rather than guessed."""
    insn = insn_at(ea)
    if insn is None:
        return True
    try:
        import ida_idp
        return bool(insn.get_canon_feature() & ida_idp.CF_STOP)
    except Exception:
        return is_return(ea)


def transfer_target(ea):
    """Where ea hands control to other code, if anywhere: the callee of a call, or
    the destination of an unconditional branch. Branches count as much as calls -
    these helper chains are built out of tail branches."""
    if is_call(ea):
        t = idc.get_operand_value(ea, 0)
        return t if t > 0 else None
    if not stops_flow(ea) or is_return(ea):
        return None
    for t in idautils.CodeRefsFrom(ea, 0):
        return t
    return None


def walk_limit(ea):
    """How far a linear walk from ea may run: the end of its function, or the next
    function start if it has none.

    Load-bearing, not tidiness. A `bl` does not stop flow, so without a limit the
    walk leaves an 8-byte throw thunk through its own call and carries straight on
    into the NEXT thunk, which raises a different exception - and then names the
    first one after the second's class. These thunks sit in a packed row, so that
    is the normal case rather than an edge one."""
    f = ida_funcs.get_func(ea)
    if f is not None and f.start_ea <= ea < f.end_ea:
        return f.end_ea
    nxt = ida_funcs.get_next_func(ea)
    return nxt.start_ea if nxt is not None else idaapi.BADADDR


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
        for ea in frontier:
            name = (idc.get_name(ea) or "").lstrip(".")
            for symbol, role in CHAIN_TERMINALS.items():
                if name.startswith(symbol):
                    terminals.append(role)
            cur = ea
            limit = walk_limit(ea)
            for _i in range(NAME_CHAIN_INSNS):
                if cur >= limit or not idc.is_code(ida_bytes.get_full_flags(cur)):
                    break
                strings.extend(strings_at(cur))
                target = transfer_target(cur)
                if target is not None and target not in seen:
                    seen.add(target)
                    nxt.append(target)
                if stops_flow(cur):
                    break
                cur = idc.next_head(cur)
                if cur == idaapi.BADADDR:
                    break
        frontier = nxt
    return strings, terminals


def helper_role(strings, terminals):
    """A name saying what a helper does, from the evidence chain_evidence collected,
    or None to leave its name alone.

    The one thing an IL2CPP throw thunk always does is name its exception class,
    because the runtime looks the class up by string when it raises. Following the
    chain to that string is what turns `sub_278C014()` at a call site into
    `il2cpp_throw_NullReferenceException()` - and a compiler-inserted null check
    into something a reader recognises as one.

    Takes the evidence rather than an address so the caller can report on what was
    found when nothing names it."""
    for s in strings:
        if EXCEPTION_NAME.match(s):
            return "il2cpp_throw_" + s
    return terminals[0] if terminals else None


def is_generic_name(ea):
    """Is the name at ea one this script may overwrite?"""
    name = idc.get_name(ea) or ""
    return not name or name.startswith(GENERIC_PREFIXES)


def call_target(ea):
    """Where this instruction calls, if it calls."""
    if ea == idc.BADADDR:
        return None
    for xref in idautils.XrefsFrom(ea, 0):
        if xref.type in (idaapi.fl_CN, idaapi.fl_CF):
            return xref.to
    return None


def call_after(ea):
    for _ in range(ROLE_SITE_INSNS):
        if ea == idc.BADADDR:
            return None
        target = call_target(ea)
        if target is not None:
            return target
        ea = idc.next_head(ea)
    return None


def call_before(ea):
    for _ in range(ROLE_SITE_INSNS):
        ea = idc.prev_head(ea)
        if ea == idc.BADADDR:
            return None
        target = call_target(ea)
        if target is not None:
            return target
    return None


def sites_referring_to(ea, want_call):
    """The instructions referring to ea - calls to a function, or the page-pair loads
    that take the address of a metadata slot."""
    if want_call:
        return list(idautils.CodeRefsTo(ea, 0))
    # A metadata slot is pointed at by other metadata tables too. Those refs come from
    # data rather than from an instruction, and say nothing about callers.
    return [ref for ref in idautils.DataRefsTo(ea)
            if idc.is_code(idc.get_full_flags(ref))]


def agreed_target(sites, pick):
    """The address most of these call sites agree on, its share, and the runners-up.

    A vote rather than a rule: no single site proves what a helper is, but a few
    hundred sites all reaching the same address does."""
    tally = {}
    for ea in sites:
        target = pick(ea)
        if target is not None:
            tally[target] = tally.get(target, 0) + 1
    if not tally:
        return None, 0, []
    ranked = sorted(tally, key=lambda a: (-tally[a], a))
    return ranked[0], tally[ranked[0]], [(a, tally[a]) for a in ranked[1:3]]


def sample_sites(addresses, want_call):
    sites = []
    for ea in addresses:
        sites.extend(sites_referring_to(ea, want_call))
        if len(sites) >= ROLE_SAMPLE:
            return sites[:ROLE_SAMPLE], True
    return sites, False


def thunk_target(ea):
    f = ida_funcs.get_func(ea)
    if f is None or not (f.flags & idaapi.FUNC_THUNK):
        return None
    for xref in idautils.XrefsFrom(f.start_ea, 0):
        if xref.type in (idaapi.fl_JN, idaapi.fl_JF, idaapi.fl_CN, idaapi.fl_CF):
            return xref.to
    return None


def name_codegen_helpers():
    """Name the two helpers the chain walk cannot: neither one raises anything, so
    there is no exception class anywhere in their call trees to name them after.

    They are the two most-called functions in the binary and the reason an IL2CPP
    method is unreadable before this runs - every method opens with a block of them:

        sub_278BD88(&Method$List<int>.Add);        <- initialize_method_metadata
        obj = sub_27C8834(List<int>_TypeInfo);     <- object_new
        List<int>__ctor(obj, ...);                 <- and this is what `new` is

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
        ea, hits, runners_up = agreed_target(sites, pick)
        if ea is None or hits < MIN_ROLE_SITES:
            print("  %s: no agreement over %d site(s)" % (role, len(sites)))
            continue
        if ida_funcs.get_func(ea) is None:
            print("  %s: %s agreed by %d site(s), but no function there"
                  % (role, hex(ea), hits))
            continue
        print("  %s: %s, %d of %d site(s)%s" % (
            role, hex(ea), hits, len(sites), " (sampled)" if capped else ""))
        for other, count in runners_up:
            print("      runner-up %s x%d" % (hex(other), count))
        # A thunk is what the call sites hit, but the body is what a reader lands on
        # after following it, so name both.
        for target in (ea, thunk_target(ea)):
            if target is not None and is_generic_name(target):
                set_name(target, role)
        # The function the call sites reach, not the body behind it, because the next
        # pass walks call sites too and they refer to whichever one the vote found.
        found[role] = ea
    return found


def data_refs_from(ea):
    """Every data address this instruction points at."""
    return list(idautils.DataRefsFrom(ea))


def slot_loaded_before(ea, known):
    """The one metadata slot this call site loads that nothing has named yet.

    Walks back only as far as the previous call, so a slot belonging to an earlier
    statement cannot leak in, and gives up as soon as a second candidate turns up.
    Two candidates means guessing which register it went to, and a confidently wrong
    name is worse to read than the offset name it replaced."""
    found = None
    for _ in range(ROLE_SITE_INSNS):
        ea = idc.prev_head(ea)
        if ea == idc.BADADDR or call_target(ea) is not None:
            break
        for addr in data_refs_from(ea):
            if addr in known:
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
    survive every earlier pass as bare offset names:

        obj = il2cpp_codegen_object_new(qword_612E078);
        List_int___ctor(obj, ...);

    The call site says what the slot is regardless. Whatever object_new was handed,
    the constructor called straight afterwards names its class - so the second line
    is what names the first. The constructor's own symbol is not read for this: it
    may carry a metadata name, a C name from an applied type, or nothing at all,
    depending on the filters this run used. Its address in ScriptMethod is stable."""
    if object_new is None:
        return 0
    suffix = "$$.ctor"
    ctor_class = {}
    for m in data.get("ScriptMethod", []):
        if m["Name"].endswith(suffix):
            ctor_class[get_addr(m["Address"])] = m["Name"][:-len(suffix)]
    known = set()
    for table in ("ScriptMetadataMethod", "ScriptMetadata"):
        known.update(get_addr(m["Address"]) for m in data.get(table, []))
    named = 0
    for ea in sites_referring_to(object_new, True):
        target = call_after(idc.next_head(ea))
        if target is None:
            continue
        cls = ctor_class.get(target)
        if cls is None:
            continue
        slot = slot_loaded_before(ea, known)
        if slot is None:
            continue
        if idaapi.has_user_name(idc.get_full_flags(slot)):
            continue
        set_name(slot, cls + "_TypeInfo")
        # So the thousands of later sites allocating the same class stop at the
        # cheap check above instead of resolving a name each time.
        known.add(slot)
        named += 1
    return named


def name_runtime_helpers():
    """Give the runtime's shared helpers names that say what they do.

    Every other pass here is about getting the CODE right. This one is about
    getting it READABLE, which is a separate problem: a method whose body is
    `sub_278C014()` six times reads as noise, and the same method reading
    `il2cpp_throw_NullReferenceException()` six times reads as the C# that was
    actually written - where those throws are null checks nobody typed by hand.

    Candidates are the helpers already known noreturn, plus any small function
    with enough callers to be a shared one. A chain walk over all 265k methods
    would cost far more and tell us nothing: those already have real names."""
    candidates = set(known_noreturn)
    # 4 bytes per AArch64 instruction, doubled for slack on variable-length ISAs.
    limit = MAX_HELPER_INSNS * 8
    for ea in list(idautils.Functions()):
        if ea in candidates or ea in method_starts or not is_generic_name(ea):
            continue
        f = ida_funcs.get_func(ea)
        if f is None or f.end_ea - f.start_ea > limit:
            continue
        if has_enough_callers(ea):
            candidates.add(ea)
    roles = {}
    # Why the rest were not named. Without this a report of "named 0" is indefinite:
    # it could mean every helper already has a name, or that the chain walk is broken
    # and finding nothing at all. Those need opposite responses.
    outcome = {"already named": 0, "no evidence": 0, "strings, no class": 0}
    sample = []
    for ea in sorted(candidates):
        if not is_generic_name(ea):
            outcome["already named"] += 1
            continue
        strings, terminals = chain_evidence(ea)
        role = helper_role(strings, terminals)
        if role is None:
            key = "strings, no class" if strings else "no evidence"
            outcome[key] += 1
            if len(sample) < 3:
                sample.append((ea, strings[:4]))
            continue
        set_name(ea, role)
        roles[role] = roles.get(role, 0) + 1
    total = sum(roles.values())
    print("named %d of %d runtime helper(s) by role" % (total, len(candidates)))
    for role in sorted(roles, key=lambda r: (-roles[r], r))[:12]:
        print("  %s x%d" % (role, roles[role]))
    print("  not named: %s" % ", ".join(
        "%s %d" % (k, v) for k, v in sorted(outcome.items()) if v))
    for ea, strings in sample:
        print("  e.g. %s saw %s" % (hex(ea), strings if strings else "nothing"))
    return total


def is_generic_shared(name):
    return any(tag in name for tag in GENERIC_SHARED)


def pointed_name(tif):
    """The name of the struct a pointer points at, or the type's own name when it is not
    a pointer. Empty string when the type says nothing nameable."""
    if tif is None or tif.empty():
        return ""
    if tif.is_ptr():
        pointed = tif.get_pointed_object()
        if pointed is None:
            return ""
        return pointed.get_type_name() or pointed.dstr() or ""
    return tif.get_type_name() or tif.dstr() or ""


def class_pointer(tif):
    """The type itself when it points at one concrete IL2CPP class, else None.

    Il2CppDumper names every object struct `<Class>_o`, so the suffix is what separates a
    class pointer from `Il2CppObject *`, `MethodInfo *` and the primitives - none of which
    tell you which class a variable holds."""
    if tif is None or not tif.is_ptr():
        return None
    inner = pointed_name(tif)
    if not inner.endswith("_o") or is_generic_shared(inner):
        return None
    return tif


def worth_replacing(tif):
    """Whether a class pointer would tell the reader more than the variable's current
    type."""
    if tif is None or tif.empty():
        return True
    # A fixed-size stack slot is the enumerator case: scalars sharing the struct's bytes,
    # where writing a struct over one of them overlaps its neighbours. Leave those to Y.
    if tif.is_array():
        return False
    # A pointer needs the width of one. Anything narrower is not holding an object.
    if tif.get_size() != 8:
        return False
    inner = pointed_name(tif)
    if is_generic_shared(inner):
        return True
    return any(inner.startswith(v) for v in VAGUE_TYPES)


def unwrap_casts(e):
    """The variable underneath however many casts Hex-Rays put in front of it. A cast is
    exactly the shape this pass exists to remove: it means the decompiler knows the right
    type at the call and declined to write it onto the variable."""
    while e is not None and e.op in (ida_hexrays.cot_cast, ida_hexrays.cot_ref):
        e = e.x
    return e


class CallArgVotes(ida_hexrays.ctree_visitor_t):
    """One vote per call argument: the callee's declared parameter type, cast at the
    variable that was passed there.

    Every vote in the function is collected before anything is written, because a variable
    reaching two different classes is the one case where retyping actively hurts. That
    variable is two unrelated ones the decompiler merged into a single slot, so either
    type is wrong for half its uses. Those are counted and skipped."""

    def __init__(self, cfunc):
        ida_hexrays.ctree_visitor_t.__init__(self, ida_hexrays.CV_FAST)
        self.cfunc = cfunc
        self.votes = {}

    def visit_expr(self, e):
        if e.op != ida_hexrays.cot_call or e.a is None:
            return 0
        details = idaapi.func_type_data_t()
        ftype = e.a.functype
        if ftype is None or not ftype.get_func_details(details):
            return 0
        lvars = self.cfunc.get_lvars()
        for i in range(min(len(details), e.a.size())):
            want = class_pointer(details[i].type)
            if want is None:
                continue
            arg = unwrap_casts(e.a[i])
            if arg is None or arg.op != ida_hexrays.cot_var:
                continue
            idx = arg.v.idx
            lvar = lvars[idx]
            if lvar.is_arg_var or not worth_replacing(lvar.tif):
                continue
            self.votes.setdefault(idx, {})[want.dstr()] = want
        return 0


def set_lvar_type(func_ea, lvar, tif):
    lsi = ida_hexrays.lvar_saved_info_t()
    lsi.ll = lvar
    lsi.type = ida_hexrays.tinfo_t(tif)
    return ida_hexrays.modify_user_lvar_info(func_ea, ida_hexrays.MLI_TYPE, lsi)


def retype_locals():
    """Push each callee's parameter types back onto the caller's variables.

    Scoped to RETYPE_FILTER because seeing a function's variables means decompiling it.
    That is the only pass here that costs per-function time rather than per-binary time."""
    if not RETYPE_FILTER:
        print("locals: RETYPE_FILTER is empty, none retyped.")
        return
    if not ida_hexrays.init_hexrays_plugin():
        print("locals: no decompiler available, none retyped.")
        return
    wanted = {}
    for m in data.get("ScriptMethod", []):
        if not any(k in m["Name"] for k in RETYPE_FILTER):
            continue
        ea = get_addr(m["Address"])
        f = ida_funcs.get_func(ea)
        if f is not None and f.start_ea == ea:
            wanted[ea] = ea
    if not wanted:
        print("locals: RETYPE_FILTER matched no method that is its own function start.")
        return

    retyped = 0
    touched = 0
    ambiguous = 0
    failed = 0
    unreadable = []
    for ea in wanted:
        try:
            cfunc = ida_hexrays.decompile(ea)
        except Exception as e:
            unreadable.append((ea, "%s" % e))
            continue
        if cfunc is None:
            unreadable.append((ea, "decompile returned nothing"))
            continue
        walker = CallArgVotes(cfunc)
        walker.apply_to(cfunc.body, None)
        lvars = cfunc.get_lvars()
        before = retyped
        for idx, choices in walker.votes.items():
            if len(choices) != 1:
                ambiguous += 1
                continue
            tif = list(choices.values())[0]
            if set_lvar_type(ea, lvars[idx], tif):
                retyped += 1
            else:
                failed += 1
        if retyped > before:
            touched += 1

    print("locals: %d retyped across %d of %d function(s)"
          % (retyped, touched, len(wanted)))
    # Never silently. The skipped ones are exactly the variables still worth a look by
    # hand, so a count of zero and a count of forty mean very different things.
    if ambiguous:
        print("  %d left alone: one slot, two classes" % ambiguous)
    if failed:
        print("  %d refused by the decompiler" % failed)
    if unreadable:
        print("  %d would not decompile, first 5:" % len(unreadable))
        for ea, why in unreadable[:5]:
            print("    %s %s" % (hex(ea), why))


def mark_noreturn(starts, label):
    marked = 0
    for target, count in find_noreturn(starts):
        if accept_helper(target, "precedes %d function starts" % count):
            marked += 1
    marked += find_thunk_helpers()
    if marked:
        ida_auto.auto_wait()
    print("noreturn %s: %d helper(s) marked" % (label, marked))
    return marked


def restore_noreturn():
    """Re-flag helpers that this pass deleted and rebuilt.

    FUNC_NORET belongs to the function, not to the address, so a helper that is
    also a metadata start loses the flag the moment its bounds are corrected -
    del_func plus add_func gives back a function with default flags. Left alone
    the next round rediscovers it, which looks like progress and stops the rounds
    from ever converging, while every method calling it stays merged in between."""
    n = 0
    for ea in sorted(known_noreturn):
        f = ida_funcs.get_func(ea)
        if f is None or f.start_ea != ea or (f.flags & idaapi.FUNC_NORET):
            continue
        f.flags |= idaapi.FUNC_NORET
        if ida_funcs.update_func(f):
            n += 1
    return n


def classify_failures(failed):
    """Why each address still has no function of its own, in disjoint buckets so
    the counts always add up to the total."""
    tally = {}
    for ea in failed:
        if not idc.is_code(ida_bytes.get_full_flags(ea)):
            key = "still not code"
        else:
            # get_func follows a tail chunk to its owner, so it cannot tell the
            # two apart - get_fchunk is what reports the chunk the address is in.
            chunk = ida_funcs.get_fchunk(ea)
            if chunk is None:
                key = "code, claimed by nothing"
            elif chunk.flags & idaapi.FUNC_TAIL:
                key = "a tail chunk of another function"
            elif chunk.start_ea != ea:
                key = "inside another function's body"
            else:
                key = "already its own function"
        tally[key] = tally.get(key, 0) + 1
    return tally


def stated_end(start):
    """May this pass overrule IDA about where the function at `start` ends?"""
    return TRUST_ENDS and start in method_starts


def fix_bounds(starts, label):
    dissolved = 0
    for ea in starts:
        if not stated_end(ea):
            continue
        f = ida_funcs.get_func(ea)
        # Upstream handled only the start_ea == ea case (delete, then recreate
        # with metadata bounds). This is the other case: ea sits *inside* a
        # function that began earlier, so nothing was deleted and add_func lost.
        if f is not None and f.start_ea != ea:
            if ida_funcs.del_func(f.start_ea):
                dissolved += 1

    created = trimmed = 0
    how_many = {}
    failed = []
    for i in range(len(starts) - 1):
        start, end = starts[i], starts[i + 1]
        stated = stated_end(start)
        f = ida_funcs.get_func(start)
        if f is not None and f.start_ea == start:
            if f.end_ea == end or not stated:
                continue
            ida_funcs.del_func(start)
        # Without a stated end the bounds are left to flow analysis, which also
        # withholds every destructive step in the ladder - they all need an `end`.
        how = create_function(start, end) if stated else create_function(start)
        if how is None:
            failed.append(start)
            continue
        created += 1
        how_many[how] = how_many.get(how, 0) + 1
        # add_func still follows flow and can run past `end` - a call to a throw
        # helper that could not be marked noreturn has no `ret` to stop it. For a
        # method the metadata end is authoritative, so state it instead of hoping.
        # Otherwise `end` is only the next entry point, so it is a ceiling: clamp a
        # function that overran it and leave a shorter one exactly as it is. Growing
        # one to meet a distant `end` is what swallows the functions in between.
        f = ida_funcs.get_func(start)
        if f is None or f.end_ea == end:
            continue
        if (stated or f.end_ea > end) and ida_funcs.set_func_end(start, end):
            trimmed += 1
    restored = restore_noreturn()
    print("bounds %s: %d merged dissolved, %d created, %d trimmed to metadata, "
          "%d failed" % (label, dissolved, created, trimmed, len(failed)))
    if how_many:
        print("  created by: %s"
              % ", ".join("%s %d" % (k, v) for k, v in sorted(how_many.items())))
    if restored:
        print("  re-flagged %d noreturn helper(s) rebuilt by this pass" % restored)
    if failed:
        print("  of those: %s"
              % ", ".join("%d %s" % (v, k)
                          for k, v in sorted(classify_failures(failed).items(),
                                             key=lambda kv: -kv[1])))
        print("  first 5: %s" % ", ".join(hex(ea) for ea in failed[:5]))
    return len(failed)


def assert_stated_bounds(starts):
    """Re-assert the ends the metadata states, after everything that can undo them.

    Marking a callee noreturn makes IDA re-analyse every call site, and re-analysis
    recomputes the CALLER's end from flow. So an end set from the metadata is cut back
    again by the next noreturn flag - including a correct one, and including the flags
    restore_noreturn re-applies at the end of the very bounds pass that just set the
    end. The pass undoes its own work.

    What is left is an end derived from flow, which for IL2CPP is always too early: a
    method's exception-handling blocks are entered by the unwinder through the LSDA
    tables rather than by a branch, so no flow walk reaches them and everything from
    the first one on falls outside the function.

    Measured on GenLevelController$$ShuffleItemVisual: a third of the method, ending
    on the byte after a call treated as terminal, with the shuffle it exists to do
    outside any function and so out of the decompiler's reach.

    Runs dead last and only grows: nothing here creates, deletes, names or flags."""
    # IDA does not act on a noreturn flag when it is set: it queues the re-analysis, and
    # the queue drains in the background - after this script has returned. So this pass,
    # and every check before it, would see ends that are still the ones the metadata
    # dictated, report nothing wrong, and the cutting back would happen afterwards where
    # no pass can answer it. Waiting for the queue brings the damage forward to a point
    # where it can still be undone.
    #
    # Waiting is enough here, where the Ghidra script has to switch an analyzer off
    # instead: its equivalent queue is drained by re-running every analyzer over each
    # redecoded body, which on this binary did not finish in four hours. IDA's noreturn
    # re-analysis is confined to the callers, and the driver already waits for it twice.
    ida_auto.auto_wait()

    widened = 0
    for i in range(len(starts) - 1):
        start, end = starts[i], starts[i + 1]
        if not stated_end(start):
            continue
        f = ida_funcs.get_func(start)
        if f is None or f.start_ea != start or f.end_ea >= end:
            continue
        if ida_funcs.set_func_end(start, end):
            widened += 1
    print("stated bounds: %d method(s) widened back to their metadata extent"
          % widened)
    return widened


path = idaapi.ask_file(False, "*.json", "script.json from Il2cppdumper")
hpath = idaapi.ask_file(False, "*.h", "il2cpp.h from Il2cppdumper") if APPLY_TYPES else None

with open(path, "rb") as fh:
    data = json.loads(fh.read().decode("utf-8"))

# --- step 1: types ------------------------------------------------------------

# parse_decls/parse_decl/apply_type are injected into a script's namespace by
# IDA, like upstream relies on. Left unqualified deliberately: the module they
# actually live in has moved between IDA versions.
if APPLY_TYPES:
    print("Parsing il2cpp.h (slow, be patient)...")
    parse_decls(load_header(hpath), 0)
else:
    print("APPLY_TYPES is False: reusing the types already in this database.")

# --- step 2: noreturn helpers -------------------------------------------------

TRUST_ENDS = bool(data.get("Addresses"))
starts = set(get_addr(a) for a in data.get("Addresses", []))
# Addresses is meant to cover every method, but a method missing from it would
# otherwise get no function created at all.
starts |= set(get_addr(m["Address"]) for m in data.get("ScriptMethod", []))
starts = sorted(starts)
metadata_starts.update(starts)
method_starts.update(get_addr(m["Address"]) for m in data.get("ScriptMethod", []))
print("target: %s, %d-bit" % (PROCNAME, 64 if IS64 else 32))
print("%d function starts from metadata, %d of them named methods"
      % (len(starts), len(method_starts)))
print("Ends are stated for the methods and clamped for the rest - see method_starts.")
if not TRUST_ENDS:
    print("script.json has no Addresses array (older Il2CppDumper). Function ends")
    print("will be clamped to the next method rather than stated - a method with")
    print("non-method code after it keeps whatever end flow analysis chose.")

unmark_methods()
unmark_thin_helpers()
mark_noreturn(scan_set(starts), "round 1")

# --- step 3: function bounds, and further noreturn rounds ---------------------
#
# Fixing the bounds also promotes the throw helpers to functions of their own,
# which is what lets a later round mark any that were buried inside a merged
# function. Runs to a fixpoint instead of a fixed number of passes: each helper
# marked can expose the next one.

fix_bounds(starts, "pass 1")

for round_no in range(2, NORET_ROUNDS + 1):
    if not mark_noreturn(scan_set(starts), "round %d" % round_no):
        break
    fix_bounds(starts, "pass %d" % round_no)

# --- step 4: names, signatures, strings, metadata -----------------------------

type_failures = []
typed_total = 0

for m in data.get("ScriptMethod", []):
    addr = get_addr(m["Address"])
    set_name(addr, m["Name"])
    if APPLY_TYPES:
        sig = sanitise_signature(m["Signature"])
        typed_total += 1
        if apply_type(addr, parse_decl(sig, 0), 1) is False:
            type_failures.append((addr, sig))

for i, s in enumerate(data.get("ScriptString", []), start=1):
    addr = get_addr(s["Address"])
    idc.set_name(addr, "StringLiteral_%d" % i, idc.SN_NOWARN)
    idc.set_cmt(addr, s["Value"], 1)

for meta in data.get("ScriptMetadata", []):
    addr = get_addr(meta["Address"])
    set_name(addr, meta["Name"])
    idc.set_cmt(addr, meta["Name"], 1)
    if APPLY_TYPES and meta.get("Signature") is not None:
        sig = sanitise_signature(meta["Signature"])
        typed_total += 1
        if apply_type(addr, parse_decl(sig, 0), 1) is False:
            type_failures.append((addr, sig))

for meta in data.get("ScriptMetadataMethod", []):
    addr = get_addr(meta["Address"])
    # Every one of these names ends in "()", so a read of the slot prints as
    # `v3 = Method$List<int>.Add();` - a call that is not there. The comment keeps the
    # name whole; the label is what the decompiler puts in the expression.
    set_name(addr, meta["Name"][:-2] if meta["Name"].endswith("()") else meta["Name"])
    idc.set_cmt(addr, meta["Name"], 1)
    idc.set_cmt(addr, "{0:X}".format(get_addr(meta["MethodAddress"])), 0)

if APPLY_TYPES:
    if type_failures:
        print("signatures: %d of %d failed, first 10:"
              % (len(type_failures), typed_total))
        for addr, sig in type_failures[:10]:
            print("  %s %s" % (hex(addr), sig))
    else:
        print("signatures: all %d applied." % typed_total)

# --- step 5: function bounds, final pass --------------------------------------
#
# apply_type marks functions dirty, so the mass typing above queues reanalysis -
# and reanalysis re-propagates flow across any throw call still not marked
# noreturn, re-merging functions that pass 1 had just separated. So bounds have
# to be the LAST thing done, after the queue drains.

print("Waiting for reanalysis to drain...")
ida_auto.auto_wait()
fix_bounds(starts, "final")

# --- step 6: readable names for the runtime's helpers -------------------------
#
# Last, so the candidate set is the final function list rather than an
# intermediate one. Nothing after this depends on it: it only renames.

# First, so the chain walk below sees them as named and does not try to explain two
# functions that raise nothing in terms of an exception class further down their trees.
print("codegen helpers:")
helpers = name_codegen_helpers()
print("  type slots: %d named from their constructor"
      % name_type_slots(helpers.get("il2cpp_codegen_object_new")))
name_runtime_helpers()

# --- step 7: put back the flow the noreturn flags cut -------------------------
#
# After the last noreturn round, so refused_methods holds every method the shape rule
# offered up rather than only those seen by round 1.
#
# Before the widening, which is the opposite order to the Ghidra script - on purpose.
# set_func_end will not grow a function over bytes that are not code, so the holes
# have to be decoded first; Ghidra's setBody has no such objection, and there the
# walk that decodes them is confined to the body, so the body has to be right first.

repair_queued_flow(starts)

# --- step 8: re-assert the stated bounds --------------------------------------
#
# Last, because every noreturn flag set above cuts back the end of the functions
# that call it. Anything earlier gets undone by what follows it - including by IDA's
# own queued re-analysis, which is why the pass starts by waiting for it.

assert_stated_bounds(starts)

# --- step 9: locals -----------------------------------------------------------
#
# Last of the passes that change what the decompiler prints, and deliberately after the
# bounds work: a function whose body still ends early decompiles to a different set of
# variables, so voting on them before step 8 would type variables that stop existing.

retype_locals()

# --- step 10: self-check ------------------------------------------------------
#
# The failure this script exists to prevent is silent: F5 hands you a different
# method's body and the only clue is the title. So assert the invariant that
# makes F5 trustworthy - every named method must be the START of its function.

bad = []
for m in data.get("ScriptMethod", []):
    addr = get_addr(m["Address"])
    f = ida_funcs.get_func(addr)
    if f is None or f.start_ea != addr:
        chunk = ida_funcs.get_fchunk(addr)
        if chunk is None:
            where = "no function"
        elif chunk.flags & idaapi.FUNC_TAIL:
            where = "a tail chunk of %s" % hex(chunk.owner)
        else:
            where = "inside %s" % hex(chunk.start_ea)
        bad.append((addr, m["Name"], where))

if bad:
    print("WARNING: %d method(s) are not their own function start." % len(bad))
    print("Decompiling these gives you the WRONG function. First 10:")
    for addr, name, where in bad[:10]:
        print("  %s %s (%s)" % (hex(addr), name, where))
else:
    print("Self-check passed: every method is its own function start.")

print("Script finished! Wait for AU: idle in the status bar, then save.")
