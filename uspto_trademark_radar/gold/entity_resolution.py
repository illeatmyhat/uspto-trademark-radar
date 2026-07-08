"""Multi-signal entity resolution for filing operations.

Two grouping signals the corpus reliably carries:

1. **Correspondent street address**, normalized to an alphanumeric key.
2. **Canonical attorney name**, via `normalize.canonical_attorney`.

`operation_key` picks one (address, else name) for simple per-filing keying.
`operation_keys` is the grouping used by operation_profile: each filing is
keyed on its most reliable *non-hub* signal — the canonical attorney name
first (a licensed attorney is sticky and resists the cheap address-rotation
evasion), the correspondent address second. Hub signals are excluded: a name
appearing across more than `NAME_HUB_MAX_ADDRESSES` addresses (a common name
or a shared filing-service attorney) and an address serving more than
`ADDR_HUB_MAX_NAMES` names (a mailbox / registered-agent) are unreliable, so
a filing carrying only a hub signal falls through to the next.

Grouping is deliberately NON-transitive (no connected components): linking
filings that share *either* signal transitively chains unrelated operations
through common names/addresses into a single corpus-wide blob (measured: a
3.6M-filing component), which would falsely merge innocent filers with mills
- the worse error here. Non-transitive keying caps a group at one signal's
real usage. The cost is the opposite failure: an operation that rotates BOTH
its addresses and its attorneys, sharing no repeated non-hub signal,
fragments. That requires many licensed attorneys (expensive) and fragmenting
is the safe direction (under-merge, not false-merge).

Every key is a candidate-grouping aid, NOT a person identifier - see the
known-limitation note in `normalize.py`. Grouping (including any use of the
attorney name) is confined to the LOCAL operation_profile table
(docs/adr/0008); it is never published. The corpus carries no attorney bar
number, deposit account, or e-signature, so no such identifier is used.

Keep every group auditable back to its raw name/address variants before
reading anything into it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..normalize import canonical_attorney

if TYPE_CHECKING:
    import duckdb

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def address_key(*lines: str | None) -> str | None:
    """Alphanumeric key over the given address lines; None when nothing
    usable remains (missing or punctuation-only address)."""
    joined = " ".join(line for line in lines if line)
    return _NON_ALNUM.sub("", joined.upper()) or None


def operation_key(attorney_name: str | None,
                  *address_lines: str | None) -> str | None:
    """Group key for one filing: address if present, else canonical attorney
    name, else None (filing cannot be attributed to an operation)."""
    addr = address_key(*address_lines)
    if addr:
        return f"ADDR:{addr}"
    canon = canonical_attorney(attorney_name)
    if canon:
        return f"NAME:{canon}"
    return None


# A name appearing across more distinct addresses than this is a common name
# or shared filing-service attorney - unreliable as an operation key.
NAME_HUB_MAX_ADDRESSES = 50
# An address serving more distinct names than this is a mailbox / agent.
ADDR_HUB_MAX_NAMES = 25


def operation_keys(
    rows: list[tuple[str, str | None, str | None]],
    name_hub_max_addresses: int = NAME_HUB_MAX_ADDRESSES,
    addr_hub_max_names: int = ADDR_HUB_MAX_NAMES,
) -> dict[str, str]:
    """Non-transitive operation grouping (see module docstring).

    `rows` are (serial_no, address_key, canonical_name); either key may be
    None. Returns serial_no -> operation key: `N:<name>` for a non-hub name,
    else `A:<addr>` for a non-hub address, else whichever signal is present.
    Deterministic (depends only on the row set, not its order). Filings with
    neither signal are omitted.
    """
    name_addrs: dict[str, set[str]] = {}
    addr_names: dict[str, set[str]] = {}
    for _, addr, name in rows:
        if addr and name:
            name_addrs.setdefault(name, set()).add(addr)
            addr_names.setdefault(addr, set()).add(name)

    def name_ok(name: str | None) -> bool:
        return bool(name) and len(name_addrs.get(name, ())) <= name_hub_max_addresses

    def addr_ok(addr: str | None) -> bool:
        return bool(addr) and len(addr_names.get(addr, ())) <= addr_hub_max_names

    out: dict[str, str] = {}
    for serial, addr, name in rows:
        if name_ok(name):
            out[serial] = f"N:{name}"
        elif addr_ok(addr):
            out[serial] = f"A:{addr}"
        elif name:
            out[serial] = f"N:{name}"
        elif addr:
            out[serial] = f"A:{addr}"
    return out


def register(con: duckdb.DuckDBPyConnection) -> None:
    """Register the resolution primitives as duckdb UDFs: canon_fn(name),
    addr_key_fn(a2, a3, a4), op_key_fn(name, a2, a3, a4)."""
    from duckdb.func import FunctionNullHandling

    con.create_function("canon_fn", canonical_attorney, ["VARCHAR"],
                        "VARCHAR", null_handling=FunctionNullHandling.SPECIAL)
    con.create_function(
        "addr_key_fn", lambda a2, a3, a4: address_key(a2, a3, a4),
        ["VARCHAR", "VARCHAR", "VARCHAR"], "VARCHAR",
        null_handling=FunctionNullHandling.SPECIAL)
    con.create_function(
        "op_key_fn",
        lambda name, a2, a3, a4: operation_key(name, a2, a3, a4),
        ["VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR"],
        "VARCHAR", null_handling=FunctionNullHandling.SPECIAL)
