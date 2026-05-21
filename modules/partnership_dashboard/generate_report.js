/**
 * Investor Report Generator — Partnership Dashboard
 *
 * Reads JSON dashboard data from stdin, produces a polished .docx report.
 * Usage: echo '{"scenario": {...}}' | node generate_report.js output.docx
 */

const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle,
  WidthType, ShadingType, PageBreak, PageNumber, LevelFormat
} = require('docx');

// ─── Helpers ─────────────────────────────────────────────────────

function fmtUSD(v) {
  if (v == null) return '—';
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
  if (abs >= 1e3) return sign + '$' + Math.round(abs).toLocaleString();
  return sign + '$' + abs.toFixed(0);
}

function fmtPct(v) {
  if (v == null) return '—';
  return (v * 100).toFixed(2) + '%';
}

function fmtX(v) {
  if (v == null) return '—';
  return v.toFixed(2) + 'x';
}

function fmtK(v) {
  if (v == null) return '—';
  return '$' + Math.round(v / 1000).toLocaleString() + 'K';
}

// ─── Styles & Constants ──────────────────────────────────────────

const BRAND_NAVY = '0B3D6B';
const BRAND_PRIMARY = '185FA5';
const BRAND_LIGHT = 'D5E8F0';
const BRAND_CLOUD = 'E6F1FB';
const GRAY = '666666';
const LIGHT_GRAY = 'F5F5F5';

const PAGE_WIDTH = 9360; // US Letter with 1" margins
const FULL_BORDER = { style: BorderStyle.SINGLE, size: 1, color: 'CCCCCC' };
const BORDERS = { top: FULL_BORDER, bottom: FULL_BORDER, left: FULL_BORDER, right: FULL_BORDER };
const CELL_MARGINS = { top: 60, bottom: 60, left: 100, right: 100 };

const HEADER_CELL_SHADING = { fill: BRAND_NAVY, type: ShadingType.CLEAR };
const ALT_ROW_SHADING = { fill: LIGHT_GRAY, type: ShadingType.CLEAR };

function headerRun(text) {
  return new TextRun({ text, bold: true, color: 'FFFFFF', font: 'Arial', size: 18 });
}

function cellRun(text, opts = {}) {
  return new TextRun({ text: String(text), font: 'Arial', size: 18, ...opts });
}

function labelRun(text) {
  return new TextRun({ text, font: 'Arial', size: 18 });
}

function valueRun(text, opts = {}) {
  return new TextRun({ text: String(text), font: 'Arial', size: 18, bold: true, ...opts });
}

function spacer(height = 120) {
  return new Paragraph({ spacing: { after: height } });
}

// ─── Section Builders ────────────────────────────────────────────

function buildCoverSection(data, sc) {
  const now = new Date();
  const dateStr = now.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });

  return [
    spacer(2400),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 200 },
      children: [new TextRun({ text: data.entity_name || 'Chamberlain Apartments LLC', font: 'Arial', size: 48, bold: true, color: BRAND_NAVY })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 100 },
      children: [new TextRun({ text: 'Partnership Investment Report', font: 'Arial', size: 32, color: BRAND_PRIMARY })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 600 },
      border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: BRAND_PRIMARY, space: 1 } },
      children: [new TextRun({ text: dateStr, font: 'Arial', size: 22, color: GRAY })],
    }),
    spacer(400),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 100 },
      children: [new TextRun({ text: `TIF Scenario: ${sc.tif_label}`, font: 'Arial', size: 24, color: GRAY })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [new TextRun({ text: `Data Source: ${sc.proforma_source === 'live' ? 'Live Proforma Engine' : 'Default Assumptions'}`, font: 'Arial', size: 20, color: GRAY })],
    }),
    new Paragraph({ children: [new PageBreak()] }),
  ];
}

function buildDealSummary(sc) {
  const dm = sc.decision_metrics;
  const pf = sc.proforma;
  const debt = sc.debt;

  const rows = [
    ['Acquisition Cost Basis', fmtUSD(pf.acquisition_cost_basis)],
    ['Total Equity', fmtUSD(pf.initial_equity)],
    ['Hold Period', `${pf.hold_years} Years`],
    ['Exit Cap Rate', fmtPct(pf.exit_cap_rate)],
    ['Gross Sale Price', fmtUSD(pf.gross_sale_price)],
    ['Net Sale Proceeds', fmtUSD(pf.net_sale_proceeds)],
    ['', ''],
    ['Deal IRR', fmtPct(dm.deal_irr)],
    ['Deal Equity Multiple', fmtX(dm.deal_em)],
    ['Total Distributions (Operations)', fmtUSD(dm.total_distributions)],
    ['Total Surplus Note Payments', fmtUSD(dm.total_surplus_note)],
    ['Total MIP', fmtUSD(dm.total_mip)],
    ['', ''],
    ['Senior Loan Balance', fmtUSD(debt.current_balance)],
    ['Interest Rate', fmtPct(debt.rate)],
    ['Annual Debt Service', fmtUSD(debt.annual_debt_service)],
    ['Min DSCR', fmtX(dm.min_dscr)],
    ['Avg DSCR', fmtX(dm.avg_dscr)],
  ];

  const colWidths = [5600, 3760];
  const tableRows = rows.map((r, i) => {
    if (r[0] === '' && r[1] === '') {
      // Spacer row
      return new TableRow({
        children: [
          new TableCell({ borders: BORDERS, width: { size: colWidths[0], type: WidthType.DXA }, margins: CELL_MARGINS, children: [new Paragraph({ children: [cellRun(' ')] })] }),
          new TableCell({ borders: BORDERS, width: { size: colWidths[1], type: WidthType.DXA }, margins: CELL_MARGINS, children: [new Paragraph({ children: [cellRun(' ')] })] }),
        ],
      });
    }
    const shading = i % 2 === 0 ? ALT_ROW_SHADING : undefined;
    return new TableRow({
      children: [
        new TableCell({ borders: BORDERS, width: { size: colWidths[0], type: WidthType.DXA }, margins: CELL_MARGINS, shading, children: [new Paragraph({ children: [labelRun(r[0])] })] }),
        new TableCell({ borders: BORDERS, width: { size: colWidths[1], type: WidthType.DXA }, margins: CELL_MARGINS, shading, children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [valueRun(r[1])] })] }),
      ],
    });
  });

  return [
    new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun('Deal Summary')] }),
    spacer(80),
    new Table({ width: { size: PAGE_WIDTH, type: WidthType.DXA }, columnWidths: colWidths, rows: tableRows }),
    spacer(200),
  ];
}

function buildPartnerReturns(sc) {
  const colWidths = [2200, 1430, 1430, 1430, 1430, 1440];
  const headerRow = new TableRow({
    children: ['Partner', 'Equity', 'EM', 'IRR', 'Avg CoC', 'Unpaid Pref'].map((h, i) =>
      new TableCell({
        borders: BORDERS, shading: HEADER_CELL_SHADING,
        width: { size: colWidths[i], type: WidthType.DXA }, margins: CELL_MARGINS,
        children: [new Paragraph({ alignment: i > 0 ? AlignmentType.RIGHT : AlignmentType.LEFT, children: [headerRun(h)] })],
      })
    ),
  });

  const dataRows = sc.partners.map((p, i) => {
    const shading = i % 2 === 0 ? ALT_ROW_SHADING : undefined;
    const vals = [p.name, fmtUSD(p.initial_equity), fmtX(p.equity_multiple), fmtPct(p.irr), fmtPct(p.avg_cash_on_cash), fmtUSD(p.unpaid_pref)];
    return new TableRow({
      children: vals.map((v, j) =>
        new TableCell({
          borders: BORDERS, shading,
          width: { size: colWidths[j], type: WidthType.DXA }, margins: CELL_MARGINS,
          children: [new Paragraph({ alignment: j > 0 ? AlignmentType.RIGHT : AlignmentType.LEFT, children: [cellRun(v, j === 0 ? { bold: true } : {})] })],
        })
      ),
    });
  });

  return [
    new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun('Partner Returns')] }),
    spacer(80),
    new Table({ width: { size: PAGE_WIDTH, type: WidthType.DXA }, columnWidths: colWidths, rows: [headerRow, ...dataRows] }),
    spacer(120),
    // Pref details
    ...sc.partners.map(p => new Paragraph({
      spacing: { after: 60 },
      children: [
        cellRun(`${p.name}: `, { bold: true }),
        cellRun(`Accrued Pref ${fmtUSD(p.accrued_pref)} | Paid ${fmtUSD(p.paid_pref)} | Unpaid ${fmtUSD(p.unpaid_pref)}`, { color: GRAY }),
      ],
    })),
    spacer(200),
  ];
}

function buildAnnualCashFlow(sc) {
  const years = sc.annual_summary;
  const colWidths = [700, 1100, 1100, 700, 1100, 1100, 1100, 1100, 1100, 1060];
  const headers = ['Year', 'NOI', 'Debt Svc', 'DSCR', 'Levered CF', 'KA Dist', 'IDP Dist', 'Note Pmt', 'KA CoC', 'IDP CoC'];

  const headerRow = new TableRow({
    children: headers.map((h, i) =>
      new TableCell({
        borders: BORDERS, shading: HEADER_CELL_SHADING,
        width: { size: colWidths[i], type: WidthType.DXA }, margins: CELL_MARGINS,
        children: [new Paragraph({ alignment: i > 0 ? AlignmentType.RIGHT : AlignmentType.CENTER, children: [headerRun(h)] })],
      })
    ),
  });

  const dataRows = years.map((yr, i) => {
    const shading = i % 2 === 0 ? ALT_ROW_SHADING : undefined;
    const vals = [
      String(yr.calendar_year), fmtK(yr.noi), fmtK(yr.debt_service),
      yr.dscr.toFixed(2) + 'x', fmtK(yr.levered_cf),
      fmtK(yr.distributions_ka), fmtK(yr.distributions_idp),
      fmtK(yr.surplus_note_payment), fmtPct(yr.coc_ka), fmtPct(yr.coc_idp),
    ];
    return new TableRow({
      children: vals.map((v, j) => {
        const dscrColor = j === 3 && yr.dscr < 1.15 ? 'CC0000' : (j === 3 && yr.dscr < 1.25 ? 'CC7700' : undefined);
        return new TableCell({
          borders: BORDERS, shading,
          width: { size: colWidths[j], type: WidthType.DXA }, margins: CELL_MARGINS,
          children: [new Paragraph({
            alignment: j > 0 ? AlignmentType.RIGHT : AlignmentType.CENTER,
            children: [cellRun(v, dscrColor ? { color: dscrColor, bold: true } : {})],
          })],
        });
      }),
    });
  });

  // Totals
  let totNoi = 0, totDs = 0, totCf = 0, totKa = 0, totIdp = 0, totNote = 0;
  years.forEach(yr => { totNoi += yr.noi; totDs += yr.debt_service; totCf += yr.levered_cf; totKa += yr.distributions_ka; totIdp += yr.distributions_idp; totNote += yr.surplus_note_payment; });
  const totVals = ['Total', fmtK(totNoi), fmtK(totDs), '—', fmtK(totCf), fmtK(totKa), fmtK(totIdp), fmtK(totNote), '—', '—'];
  const totRow = new TableRow({
    children: totVals.map((v, j) =>
      new TableCell({
        borders: BORDERS, shading: { fill: BRAND_CLOUD, type: ShadingType.CLEAR },
        width: { size: colWidths[j], type: WidthType.DXA }, margins: CELL_MARGINS,
        children: [new Paragraph({ alignment: j > 0 ? AlignmentType.RIGHT : AlignmentType.CENTER, children: [cellRun(v, { bold: true })] })],
      })
    ),
  });

  return [
    new Paragraph({ children: [new PageBreak()] }),
    new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun('Annual Cash Flow Summary')] }),
    spacer(80),
    new Table({ width: { size: PAGE_WIDTH, type: WidthType.DXA }, columnWidths: colWidths, rows: [headerRow, ...dataRows, totRow] }),
    spacer(200),
  ];
}

function buildDebtPosition(sc) {
  const debt = sc.debt;
  const dm = sc.decision_metrics;

  const rows = [
    ['Current Loan Balance', fmtUSD(debt.current_balance)],
    ['Original Principal', fmtUSD(debt.original_principal)],
    ['Interest Rate', fmtPct(debt.rate)],
    ['Monthly Payment', fmtUSD(debt.monthly_payment)],
    ['Annual Debt Service', fmtUSD(debt.annual_debt_service)],
    ['Remaining Term', `${debt.remaining_term_months} months`],
    ['', ''],
    ['MIP Rate', fmtPct(debt.mip_rate)],
    ['Year 1 MIP', fmtUSD(debt.year1_mip)],
    ['Total MIP (Hold Period)', fmtUSD(debt.total_mip_over_hold)],
    ['', ''],
    ['Min DSCR', fmtX(debt.min_dscr)],
    ['Avg DSCR', fmtX(debt.avg_dscr)],
    ['Covenant Breaches', String(debt.breach_count)],
    ['Initial LTV', fmtPct(debt.initial_ltv)],
    ['Terminal LTV', fmtPct(debt.terminal_ltv)],
  ];

  const colWidths = [5600, 3760];
  const tableRows = rows.map((r, i) => {
    if (r[0] === '' && r[1] === '') {
      return new TableRow({
        children: colWidths.map(w => new TableCell({ borders: BORDERS, width: { size: w, type: WidthType.DXA }, margins: CELL_MARGINS, children: [new Paragraph({ children: [cellRun(' ')] })] })),
      });
    }
    const shading = i % 2 === 0 ? ALT_ROW_SHADING : undefined;
    return new TableRow({
      children: [
        new TableCell({ borders: BORDERS, width: { size: colWidths[0], type: WidthType.DXA }, margins: CELL_MARGINS, shading, children: [new Paragraph({ children: [labelRun(r[0])] })] }),
        new TableCell({ borders: BORDERS, width: { size: colWidths[1], type: WidthType.DXA }, margins: CELL_MARGINS, shading, children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [valueRun(r[1])] })] }),
      ],
    });
  });

  return [
    new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun('Debt Position')] }),
    spacer(80),
    new Table({ width: { size: PAGE_WIDTH, type: WidthType.DXA }, columnWidths: colWidths, rows: tableRows }),
    spacer(200),
  ];
}

function buildScenarioComparison(data) {
  const names = data.scenario_names || [];
  if (names.length < 2) return [];

  const metrics = [
    { label: 'Deal IRR', key: 'deal_irr', fmt: fmtPct },
    { label: 'Deal EM', key: 'deal_em', fmt: fmtX },
    { label: 'KA EM', key: 'ka_em', fmt: fmtX },
    { label: 'IDP EM', key: 'idp_em', fmt: fmtX },
    { label: 'Min DSCR', key: 'min_dscr', fmt: fmtX },
    { label: 'Avg DSCR', key: 'avg_dscr', fmt: fmtX },
    { label: 'Total Distributions', key: 'total_distributions', fmt: fmtUSD },
    { label: 'Net Sale Proceeds', key: 'net_sale_proceeds', fmt: fmtUSD },
  ];

  // Build column widths: metric label + one col per scenario
  const labelWidth = 2200;
  const scenarioWidth = Math.floor((PAGE_WIDTH - labelWidth) / names.length);
  const colWidths = [labelWidth, ...names.map(() => scenarioWidth)];

  // Header
  const headerRow = new TableRow({
    children: [
      new TableCell({ borders: BORDERS, shading: HEADER_CELL_SHADING, width: { size: colWidths[0], type: WidthType.DXA }, margins: CELL_MARGINS, children: [new Paragraph({ children: [headerRun('Metric')] })] }),
      ...names.map((n, i) => new TableCell({
        borders: BORDERS, shading: HEADER_CELL_SHADING,
        width: { size: colWidths[i + 1], type: WidthType.DXA }, margins: CELL_MARGINS,
        children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [headerRun(n)] })],
      })),
    ],
  });

  // Data rows
  const dataRows = metrics.map((m, mi) => {
    const shading = mi % 2 === 0 ? ALT_ROW_SHADING : undefined;
    return new TableRow({
      children: [
        new TableCell({ borders: BORDERS, shading, width: { size: colWidths[0], type: WidthType.DXA }, margins: CELL_MARGINS, children: [new Paragraph({ children: [labelRun(m.label)] })] }),
        ...names.map((n, i) => {
          const sc = data.scenarios[n];
          const dm = sc ? sc.decision_metrics : {};
          const val = dm[m.key];
          return new TableCell({
            borders: BORDERS, shading,
            width: { size: colWidths[i + 1], type: WidthType.DXA }, margins: CELL_MARGINS,
            children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [cellRun(m.fmt(val), { bold: i === 0 })] })],
          });
        }),
      ],
    });
  });

  return [
    new Paragraph({ children: [new PageBreak()] }),
    new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun('TIF Scenario Comparison')] }),
    spacer(80),
    new Paragraph({
      spacing: { after: 120 },
      children: [cellRun('Key return and risk metrics across all Tax Increment Financing scenarios. The first column represents the baseline (no appeal) case.', { color: GRAY, italics: true })],
    }),
    new Table({ width: { size: PAGE_WIDTH, type: WidthType.DXA }, columnWidths: colWidths, rows: [headerRow, ...dataRows] }),
    spacer(200),
  ];
}

function buildDisclaimer() {
  return [
    new Paragraph({
      spacing: { before: 400 },
      border: { top: { style: BorderStyle.SINGLE, size: 1, color: 'CCCCCC', space: 8 } },
      children: [cellRun('DISCLAIMER: This report is generated from modeled projections and is intended for informational purposes only. Actual results may differ materially from projections. This does not constitute investment advice. Consult with qualified legal, tax, and financial advisors before making investment decisions.', { color: GRAY, italics: true, size: 16 })],
    }),
  ];
}

// ─── Main ────────────────────────────────────────────────────────

async function main() {
  const outputPath = process.argv[2];
  if (!outputPath) {
    console.error('Usage: node generate_report.js <output.docx>');
    process.exit(1);
  }

  // Read JSON from stdin
  let input = '';
  for await (const chunk of process.stdin) input += chunk;
  const data = JSON.parse(input);

  // Use first scenario as primary, or specified
  const scenarioName = data._primary_scenario || data.scenario_names[0];
  const sc = data.scenarios[scenarioName];
  if (!sc) {
    console.error(`Scenario "${scenarioName}" not found`);
    process.exit(1);
  }

  const now = new Date();
  const dateStr = now.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });

  const doc = new Document({
    styles: {
      default: { document: { run: { font: 'Arial', size: 22 } } },
      paragraphStyles: [
        { id: 'Heading1', name: 'Heading 1', basedOn: 'Normal', next: 'Normal', quickFormat: true,
          run: { size: 32, bold: true, font: 'Arial', color: BRAND_NAVY },
          paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 0 } },
        { id: 'Heading2', name: 'Heading 2', basedOn: 'Normal', next: 'Normal', quickFormat: true,
          run: { size: 26, bold: true, font: 'Arial', color: BRAND_PRIMARY },
          paragraph: { spacing: { before: 180, after: 100 }, outlineLevel: 1 } },
      ],
    },
    sections: [{
      properties: {
        page: {
          size: { width: 15840, height: 12240, orientation: 'landscape' },
          margin: { top: 1080, right: 1080, bottom: 1080, left: 1080 },
        },
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            alignment: AlignmentType.RIGHT,
            children: [new TextRun({ text: `${data.entity_name || 'Chamberlain Apartments LLC'} — Investor Report`, font: 'Arial', size: 16, color: GRAY })],
          })],
        }),
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            children: [
              new TextRun({ text: 'Page ', font: 'Arial', size: 16, color: GRAY }),
              new TextRun({ children: [PageNumber.CURRENT], font: 'Arial', size: 16, color: GRAY }),
              new TextRun({ text: `  |  Generated ${dateStr}`, font: 'Arial', size: 16, color: GRAY }),
            ],
          })],
        }),
      },
      children: [
        ...buildCoverSection(data, sc),
        ...buildDealSummary(sc),
        ...buildPartnerReturns(sc),
        ...buildAnnualCashFlow(sc),
        ...buildDebtPosition(sc),
        ...buildScenarioComparison(data),
        ...buildDisclaimer(),
      ],
    }],
  });

  const buffer = await Packer.toBuffer(doc);
  fs.writeFileSync(outputPath, buffer);
  console.log(`Report written to ${outputPath} (${(buffer.length / 1024).toFixed(0)} KB)`);
}

main().catch(e => { console.error(e); process.exit(1); });
