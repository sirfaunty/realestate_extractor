"""
Lease depth (extractors/articles.py): articles split in order with their
pages, whatever the numbering style; a TOC page is skipped; an OCR-lost or
misread heading does not end the run; a lost heading is found again at its
'Section 1.' restart; the category sweep cites one verbatim excerpt per
category per page. Fictional lease text.

    venv/Scripts/python tests/test_articles.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from realestate_extractor.extractors.articles import split_articles, sweep  # noqa: E402

passed = failed = 0


def test(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f'  ok   {name}')
    else:
        failed += 1
        print(f'  FAIL {name}')


TOC = ("TABLE OF CONTENTS\nArticle 1 Premises ........ 1\nArticle 2 Term ........ 2\nArticle 3 Rent ........ 2\n"
       "Article 4 Use ........ 3\nArticle 5 Signs ........ 3\nArticle 6 Insurance ........ 4\n")
P1 = ("ARTICLE 1\nPREMISES\nSection 1. Landlord leases to Tenant Suite 12 of Birch Commons.\n"
      "ARTICLE 2\nTERM\nSection 1. The term is five (5) years. Tenant shall have one option to extend the term.\n")
P2 = ("ARTICLE 3\nMINIMUM RENT\nSection 1. Tenant shall pay Minimum Rent monthly, subject to Article 7.\n"
      "ARTICLE 4\nUSE\nSection 1. Tenant shall use the Premises as a bakery. Tenant shall continuously operate.\n")
P3 = ("ARTICLE 5\nSIGNS\nSection 1. Tenant may install signage approved by Landlord.\n"
      "Section 1. Tenant shall maintain insurance naming Landlord as additional insured.\n"
      "ARTICLE 7\nHOLDING OVER\nSection 1. If Tenant holds over, Tenant is a tenant at sufferance.\n")

arts = split_articles([TOC, P1, P2, P3])
by = {a.ordinal: a for a in arts}
test('the table of contents is skipped: article 1 starts on page 2, not the TOC',
     by.get(1) and by[1].page_from == 2)
test('articles in order with their titles', [a.ordinal for a in arts][:5] == [1, 2, 3, 4, 5]
     and by[1].title == 'PREMISES' and by[3].title == 'MINIMUM RENT')
test('the body is verbatim, heading to next heading', by[2].body.startswith('ARTICLE 2\nTERM')
     and by[2].body.rstrip().endswith('option to extend the term.'))
test('a cross-reference ("subject to Article 7") is not a heading', by[3].page_from == 3 and by[3].page_to == 3)
test("an article whose heading OCR lost is found at its 'Section 1.' restart",
     by.get(6) is not None and by[6].style == 'section_restart' and 'insurance' in by[6].body)
test('the run continues past it to ARTICLE 7', by.get(7) is not None and by[7].title == 'HOLDING OVER')
misread = split_articles(["ARTICLE 1\nA\nx\nARTICLE 2\nB\nx\nARTICLE 3\nC\nx\nARTICLE 3\nD\nx\nARTICLE 5\nE\nx\n"])
test('a misread heading number (4 read as 3) does not end the run', [a.ordinal for a in misread][-1] == 5)
num = split_articles(["1. Premises. Landlord leases the store.\n2. Term. Five years.\n3. Rent. $2,000 per month.\n"])
test('numbered headings ("1. Premises.") when there is no ARTICLE style',
     [a.ordinal for a in num] == [1, 2, 3] and num[0].style == 'numbered')
test('no numbered run -> no articles', split_articles(['A letter about the parking lot.']) == [])

hits = sweep([TOC, P1, P2, P3])
cats = {(h.category, h.page) for h in hits}
test('the sweep finds each category on its page', ('Renewal / extension option', 2) in cats
     and ('Continuous operation / go-dark', 3) in cats and ('Holdover', 4) in cats and ('Insurance', 4) in cats)
test('the sweep skips the TOC page', not any(h.page == 1 for h in hits))
test('an excerpt is a verbatim slice of its page', all(h.excerpt in [TOC, P1, P2, P3][h.page - 1] for h in hits))
test('one row per (category, page)', len(cats) == len(hits))

print(f'\n{passed} passed, {failed} failed')
sys.exit(1 if failed else 0)
