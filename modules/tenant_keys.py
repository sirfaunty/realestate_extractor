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
# trade words two unrelated names can share; never a one-word match on their own
TRADE_WORDS = {'dentistry', 'dental', 'chiropractic', 'medical', 'clinic', 'family', 'properties', 'property',
               'realty', 'restaurant', 'kitchen', 'fitness', 'wireless', 'cleaners', 'liquors', 'liquor', 'salon',
               'nails', 'insurance', 'financial', 'services', 'service', 'management', 'company', 'studio',
               'market', 'grocery', 'pharmacy', 'tobacco', 'vapor', 'vapors', 'tanning', 'massage', 'therapy',
               'pediatric', 'orthodontics', 'veterinary', 'animal', 'hospital', 'church', 'school', 'academy',
               'learning', 'center', 'centre', 'coffee', 'bakery', 'pizza', 'burger', 'chicken', 'sandwich',
               'tacos', 'sushi', 'grill', 'bistro', 'express', 'national', 'american', 'northern', 'minnesota',
               'united', 'general', 'advisors', 'partners', 'holdings', 'enterprises', 'solutions', 'systems',
               'associates', 'group', 'office', 'offices', 'building', 'apartments', 'parking', 'storage'}
MIN_WITHIN = 6      # shortest roll name found inside a run-together file name
MIN_NEAR = 9        # shortest roll name matched with a misspelling
NEAR = 0.88         # share of the roll name's letters the file name must carry, in order
FILE_WEIGHT = 0.9   # a match on a document's file name, against one on a name the paper gives


@dataclass
class KeyMatch:
    tenant_key: str | None        # the roll's key (None when not on the roll)
    suite: str | None
    occupant: str | None
    basis: str                    # 'alias' | 'exact' | 'prefix' | 'words' | 'not on roll'
    score: float


# the short forms a rent roll prints for long names ("Quillon Claims Mgt Svc")
ABBREV = {'svc': 'services', 'svcs': 'services', 'srvc': 'services', 'mgt': 'management', 'mgmt': 'management',
          'intl': 'international', 'natl': 'national', 'ctr': 'center', 'cntr': 'center', 'dept': 'department',
          'assn': 'association', 'assoc': 'associates', 'bros': 'brothers', 'fc': 'force', 'nav': 'navy',
          'univ': 'university', 'med': 'medical', 'hosp': 'hospital', 'mfg': 'manufacturing', 'tech': 'technologies'}


def norm(name: str) -> str:
    """Lower case, '&' -> 'and', punctuation out, one space between words,
    roll abbreviations written out."""
    s = (name or '').lower().replace('&', ' and ')
    s = re.sub(r"[’']", '', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return ' '.join(ABBREV.get(w, w) for w in s.split())


def _stem(w: str) -> str:
    """A plural and its singular are one word ("Vapors" / "Vapor")."""
    return w[:-1] if len(w) > 4 and w.endswith('s') and not w.endswith('ss') else w


def words(name: str) -> set:
    """Distinctive words: no legal suffixes, no store numbers or codes."""
    return {_stem(w) for w in norm(name).split() if w not in LEGAL and len(w) >= 3 and not re.search(r'\d', w)}


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
    """tenancies[i] = (lease_id, names) or (lease_id, names, file_names, rank):
    a match on a file name scores FILE_WEIGHT of the same match on a name the
    paper gives; on a tie the lower rank wins (the tenancy that holds the
    lease before one that holds only letters)."""
    alias = alias or {}
    occupied = [r for r in roll if r.get('tenant_key')]
    out = {t[0]: KeyMatch(None, None, None, 'not on roll', 0.0) for t in tenancies}
    # alias first: a name the owner's table already resolves
    by_key = {}
    for r in occupied:
        by_key.setdefault(r['tenant_key'], r)
    cands = []
    for t in tenancies:
        lid, names = t[0], list(t[1])
        files = list(t[2]) if len(t) > 2 else []
        rank = t[3] if len(t) > 3 else 0
        for n in names + files:
            k = alias.get(norm(n)) or alias.get(norm(n)[:27])
            if k and k in by_key:
                cands.append((1.1 * (FILE_WEIGHT if n in files and n not in names else 1), rank, lid, k, 'alias'))
        for r in occupied:
            occ = r.get('occupant') or ''
            best = max([_score(n, occ) for n in names]
                       + [(sc * FILE_WEIGHT, bs) for sc, bs in (_score(f, occ) for f in files)], default=(0.0, ''))
            if best[0] > 0:
                cands.append((best[0], rank, lid, r['tenant_key'], best[1]))
    taken_lease, taken_key = set(), set()
    for score, _, lid, key, basis in sorted(cands, key=lambda c: (-c[0], c[1])):
        if lid in taken_lease or key in taken_key:
            continue
        r = by_key[key]
        out[lid] = KeyMatch(key, r.get('suite'), r.get('occupant'), basis, score)
        taken_lease.add(lid)
        taken_key.add(key)
    # last: a word that names one roll occupant and one tenancy and nothing
    # else in the building ("Shamrock Family Dentistry" on the roll, "SCOTT
    # SHAMROCK, D.D.S., P.A." on the paper): a surname, a brand
    seen = {}
    for t in tenancies:
        for n in list(t[1]) + (list(t[2]) if len(t) > 2 else []):
            for w in words(n):
                seen.setdefault(w, set()).add(('t', t[0]))
    for r in occupied:
        for w in words(r.get('occupant') or ''):
            seen.setdefault(w, set()).add(('r', r['tenant_key']))
    for w, who in seen.items():
        if len(w) < 7 or w in TRADE_WORDS or len(who) != 2:
            continue
        ts = [x for k, x in who if k == 't']
        rs = [x for k, x in who if k == 'r']
        if len(ts) == 1 and len(rs) == 1 and ts[0] not in taken_lease and rs[0] not in taken_key:
            r = by_key[rs[0]]
            out[ts[0]] = KeyMatch(rs[0], r.get('suite'), r.get('occupant'), 'word', 0.4)
            taken_lease.add(ts[0])
            taken_key.add(rs[0])
    return out
