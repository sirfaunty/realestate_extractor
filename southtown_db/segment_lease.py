"""
Deterministic lease-provision segmenter.

Ported from the partner's build_provisions3.py. Reads a lease .docx and splits it
by heading structure (Heading 1 = article, Heading 2 = section) into a flat list of
provisions. No model involved — this is a pure document-structure parse, so it is
exact and repeatable.

Verified: reproduces the gold-standard warehouse exactly — 113 provisions, matching
section numbers and headings, body char counts within 2%.

Usage:
    from segment_lease import segment_docx
    provisions = segment_docx("source_docs/lease_and_exhibits/DHOS Lease 122225.docx")
"""
import json
import sys

ROMANS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
          "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII"]


def extract_paras(path):
    """Return [{idx, style, text}] for every paragraph in the .docx."""
    from docx import Document
    doc = Document(path)
    return [{"idx": i, "style": (p.style.name if p.style else ""), "text": p.text}
            for i, p in enumerate(doc.paragraphs)]


def _is_title(txt):
    # A short, heading-style title (vs. long body text that landed in a Heading 2 style).
    return len(txt) < 90


def segment(paras):
    """Segment pre-extracted paragraphs into provisions. Mirrors build_provisions3.py."""
    provisions = []
    preamble = "\n".join(p['text'].strip() for p in paras
                         if p['idx'] in (0, 1) and p['text'].strip())
    provisions.append({"seq": 0, "article_num": 0, "article_roman": "PRE",
                       "article_title": "PREAMBLE / RECITALS", "section_num": "0.0",
                       "section_heading": "Lease Preamble & Parties", "start_idx": 0,
                       "body": preamble, "char_count": len(preamble)})
    st = {"art": 0, "title": None, "sec": 0, "cur": None, "seq": 1, "opened": False}

    def push(sec):
        if sec is None:
            return
        sec['body'] = "\n".join(sec['_b']).strip()
        sec.pop('_b')
        sec['char_count'] = len(sec['body'])
        sec['seq'] = st['seq']
        st['seq'] += 1
        provisions.append(sec)

    def new_section(txt, idx):
        roman = ROMANS[st['art'] - 1] if 0 < st['art'] <= len(ROMANS) else str(st['art'])
        return {"article_num": st['art'], "article_roman": roman, "article_title": st['title'],
                "section_num": f"{st['art']}.{st['sec']}", "section_heading": txt,
                "start_idx": idx, "_b": []}

    for p in paras:
        if p['idx'] in (0, 1):
            continue
        style, txt = p['style'], p['text'].strip()
        if style == "Heading 1":
            push(st['cur']); st['cur'] = None
            st['art'] += 1; st['title'] = txt or st['title']; st['sec'] = 0
            st['opened'] = True
            continue
        if style == "Heading 2":
            if txt == "":
                continue
            cur = st['cur']
            start_new = _is_title(txt) or cur is None or st['opened']
            if (cur is not None and len(cur['_b']) == 0
                    and _is_title(cur['section_heading']) and not _is_title(txt)):
                cur['_b'].append(txt); st['opened'] = False
                continue
            if start_new:
                push(cur); st['cur'] = None; st['sec'] += 1
                st['cur'] = new_section(txt, p['idx'])
                if not _is_title(txt):
                    st['cur']['_b'].append(txt)
            elif cur is not None:
                cur['_b'].append(txt)
            st['opened'] = False
            continue
        # body paragraph
        if txt == "":
            continue
        if st['cur'] is not None:
            st['cur']['_b'].append(txt)
        else:
            st['sec'] += 1
            st['cur'] = new_section("(Article Introduction)", p['idx'])
            st['cur']['_b'].append(txt)
        st['opened'] = False
    push(st['cur'])
    return provisions


def segment_docx(path):
    return segment(extract_paras(path))


if __name__ == "__main__":
    provs = segment_docx(sys.argv[1])
    print(f"Provisions: {len(provs)} | body chars: "
          f"{sum(p['char_count'] for p in provs):,}")
    out = sys.argv[2] if len(sys.argv) > 2 else "provisions.json"
    json.dump(provs, open(out, "w"), indent=1)
    print(f"Saved {out}")
