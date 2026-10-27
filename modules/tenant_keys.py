"""
Tenant keys: tie each tenancy the engine found to the owner's rent-roll key.

The engine names a tenancy from its paper (a slug of the trade name). An
owner's warehouse keys tenants by its rent roll: the roll's occupant name
made into a key, plus an alias table for names the roll cuts short or
changed. This maps one to the other so a published row joins the owner's
data, and keeps the engine's own key beside it (source_tenant_key).

    roll  = [{'suite': '0710', 'occupant': 'Lark Florist', 'tenant_key': 'lark_florist'}, ...]
    alias = {'lark florist and gif': 'lark_florist_and_gifts', ...}   # normalised name -> key
    match_tenant_keys(tenancies, roll, alias) -> {lease_id: KeyMatch}

A tenancy is tenancies[i] = (lease_id, [names]) where names are its trade
name, legal names and filename names. Matching, best first:
  1. alias: a name normalises to an alias-table entry
  2. exact: a name normalises to a roll occupant
  3. prefix: one normalised name is a prefix of the other (the roll cuts names)
  4. words: the distinctive words of the two names mostly coincide
  5. within / near: the roll name, spaces out, inside a name built from a
     file name that runs words together (near: allowing a misspelling)
Each roll row goes to at most one tenancy (the best-scoring one); a tenancy
with no roll row keeps its own key and says so (key_basis 'not on roll').
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

LEGAL = {'inc', 'llc', 'llp', 'lp', 'ltd', 'corp', 'corporation', 'co', 'company', 'pa', 'pc', 'pllc',
         'dba', 'the', 'a', 'of', 'incorporated', 'limited', 'partnership', 'store', 'stores', 'no'}
MIN_PREFIX = 8      # shortest normalised name a prefix match may rest on
MIN_WITHIN = 6      # shortest roll name found inside a run-together file name
MIN_NEAR = 9        # shortest roll name matched with a misspelling
NEAR = 0.88         # share of the roll name's letters the file name must carry, in order


@dataclass
class KeyMatch:
    tenant_key: str | None        # the roll's key (None when not on the roll)
    suite: str | None
    occupant: str | None
    basis: str                    # 'alias' | 'exact' | 'prefix' | 'words' | 'not on roll'
    score: float


def norm(name: str) -> str:
    """Lower case, '&' -> 'and', punctuation out, one space between words."""
    s = (name or '').lower().replace('&', ' and ')
    s = re.sub(r"[’']", '', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def words(name: str) -> set:
    """Distinctive words: no legal suffixes, no store numbers or codes."""
    return {w for w in norm(name).split() if w not in LEGAL and len(w) >= 3 and not re.search(r'\d', w)}


def core(name: str) -> str:
    """The name with legal suffixes, store numbers / codes and spaces taken
    out ("O' Reilly Auto Parts #1134" -> "oreillyautoparts")."""
    return ''.join(w for w in norm(name).split() if w not in LEGAL and not re.search(r'\d', w))


def _score(a: str, b: str) -> tuple[float, str]:
    na, nb = core(a), core(b)
    if not na or not nb:
        return 0.0, ''
    if na == nb:
        return 1.0, 'exact'
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(short) >= MIN_PREFIX and long_.startswith(short):
        return 0.9, 'prefix'
    wa, wb = words(a), words(b)
    if wa and wb:
        j = len(wa & wb) / min(len(wa), len(wb))
        if j >= 0.99 and min(len(wa), len(wb)) >= 1 and len(wa & wb) >= 1:
            # every distinctive word of the shorter name is in the longer one
            return 0.8 if len(wa & wb) >= 2 else 0.7, 'words'
    # a name built from a file name runs the words together ("LeaseDocuments
    # BirchBakery", "2013-FennelDeli"): the roll name inside it, whole, or
    # nearly whole when the file name misspells it ("MarigoldTaloring")
    roll_core = nb
    if len(roll_core) >= MIN_WITHIN:
        if roll_core in na:
            return 0.6, 'within'
        if len(roll_core) >= MIN_NEAR:
            m = SequenceMatcher(None, na, roll_core, autojunk=False)
            hit = sum(x.size for x in m.get_matching_blocks())
            if hit / len(roll_core) >= NEAR and _span(m) <= len(roll_core) + 2:
                return 0.5, 'near'
    return 0.0, ''


def _span(m) -> int:
    """How much of the longer name the matching blocks spread over."""
    blocks = [x for x in m.get_matching_blocks() if x.size]
    return (blocks[-1].a + blocks[-1].size - blocks[0].a) if blocks else 10 ** 6


def match_tenant_keys(tenancies: list, roll: list, alias: dict | None = None) -> dict:
    alias = alias or {}
    occupied = [r for r in roll if r.get('tenant_key')]
    out = {lid: KeyMatch(None, None, None, 'not on roll', 0.0) for lid, _ in tenancies}
    # alias first: a name the owner's table already resolves
    by_key = {}
    for r in occupied:
        by_key.setdefault(r['tenant_key'], r)
    cands = []
    for lid, names in tenancies:
        for n in names:
            k = alias.get(norm(n)) or alias.get(norm(n)[:27])
            if k and k in by_key:
                cands.append((1.1, lid, k, 'alias'))
        for r in occupied:
            best = max((_score(n, r.get('occupant') or '') for n in names), default=(0.0, ''))
            if best[0] > 0:
                cands.append((best[0], lid, r['tenant_key'], best[1]))
    taken_lease, taken_key = set(), set()
    for score, lid, key, basis in sorted(cands, key=lambda c: -c[0]):
        if lid in taken_lease or key in taken_key:
            continue
        r = by_key[key]
        out[lid] = KeyMatch(key, r.get('suite'), r.get('occupant'), basis, score)
        taken_lease.add(lid)
        taken_key.add(key)
    return out
