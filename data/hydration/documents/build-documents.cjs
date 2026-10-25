// Builds the synthetic Contoso Wealth documents for the Michael Scott story with docx-js.
// Usage: node build-documents.cjs <portfolio-story.json> <output-dir> [advisorName] [analystName]
// Every portfolio number comes from fabric/portfolio-story.json so Word, email and Fabric agree.
// Dates, quarters and file names move with STORY_MEETING_DATE (story-dates.cjs); unset keeps the reference story.
const fs = require("fs");
const path = require("path");
const docx = require("docx");
const { shiftText } = require("./story-dates.cjs");
const {
  Document, Packer, Paragraph, Table, TableRow, TableCell, Header, Footer, AlignmentType,
  LevelFormat, HeadingLevel, BorderStyle, WidthType, ShadingType, PageNumber, TabStopType, TabStopPosition,
} = docx;

// Every text run passes through the story-date shift.
class TextRun extends docx.TextRun {
  constructor(options) {
    super(typeof options === "string" ? shiftText(options)
      : options && typeof options.text === "string" ? { ...options, text: shiftText(options.text) } : options);
  }
}

const [storyPath, outDir, advisorName = "Alex Morgan", analystName = "Research Desk"] = process.argv.slice(2);
const story = JSON.parse(fs.readFileSync(storyPath, "utf8").replace(/^\uFEFF/, "")).michael_scott;
fs.mkdirSync(outDir, { recursive: true });

const advisorFirst = advisorName.split(" ")[0];
const usd = (n) => "$" + Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
const pct = (n) => `${Number(n).toFixed(1)}%`;
const CONTENT_WIDTH = 9360;
const border = { style: BorderStyle.SINGLE, size: 1, color: "BFBFBF" };
const borders = { top: border, bottom: border, left: border, right: border };

const run = (part) => (typeof part === "string" ? new TextRun(part) : new TextRun(part));
const p = (text, extra = {}) => new Paragraph({ spacing: { after: 120 }, children: [new TextRun({ text, ...extra })] });
const rich = (parts) => new Paragraph({ spacing: { after: 120 }, children: parts.map(run) });
const h1 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(text)] });
const bullet = (text) => new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 80 }, children: [new TextRun(text)] });
const numbered = (ref, text) => new Paragraph({ numbering: { reference: ref, level: 0 }, spacing: { after: 80 }, children: [new TextRun(text)] });

function table(rows, widths) {
  return new Table({
    width: { size: CONTENT_WIDTH, type: WidthType.DXA },
    columnWidths: widths,
    rows: rows.map((cells, r) => new TableRow({
      tableHeader: r === 0,
      children: cells.map((value, c) => new TableCell({
        borders,
        width: { size: widths[c], type: WidthType.DXA },
        shading: r === 0 ? { fill: "DCE6F1", type: ShadingType.CLEAR } : undefined,
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        children: [new Paragraph({ children: [new TextRun({ text: String(value), bold: r === 0 })] })],
      })),
    })),
  });
}

const listLevel = { level: 0, alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } };

function makeDocument(title, children) {
  return new Document({
    creator: "Contoso Wealth",
    title: shiftText(title),
    styles: {
      default: { document: { run: { font: "Arial", size: 22 } } },
      paragraphStyles: [
        { id: "Title", name: "Title", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { size: 40, bold: true, font: "Arial", color: "1F3864" }, paragraph: { spacing: { after: 120 } } },
        { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { size: 28, bold: true, font: "Arial", color: "1F3864" },
          paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 0 } },
      ],
    },
    numbering: {
      config: [
        { reference: "bullets", levels: [{ ...listLevel, format: LevelFormat.BULLET, text: "\u2022" }] },
        { reference: "topics", levels: [{ ...listLevel, format: LevelFormat.DECIMAL, text: "%1." }] },
        { reference: "actions", levels: [{ ...listLevel, format: LevelFormat.DECIMAL, text: "%1." }] },
      ],
    },
    sections: [{
      properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
      headers: { default: new Header({ children: [new Paragraph({
        border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "1F3864", space: 1 } },
        tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
        children: [new TextRun({ text: "Contoso Wealth", bold: true, color: "1F3864" }),
          new TextRun({ text: `\t${title}`, color: "595959", size: 18 })],
      })] }) },
      footers: { default: new Footer({ children: [new Paragraph({
        tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
        children: [
          new TextRun({ text: "Confidential. Synthetic demo content; not investment, tax or legal advice.", size: 16, color: "7F7F7F" }),
          new TextRun({ children: ["\tPage ", PageNumber.CURRENT], size: 16, color: "7F7F7F" }),
        ],
      })] }) },
      children,
    }],
  });
}

function ips({ version, draft, effective, taxBudget, esg, changes }) {
  const children = [
    new Paragraph({ style: "Title", children: [new TextRun(`Investment Policy Statement${draft ? " - DRAFT" : ""}`)] }),
    rich([{ text: "Client: ", bold: true }, "Michael Scott (client ID C0007), Private Client since March 2014, Scranton, PA"]),
    rich([{ text: "Version: ", bold: true }, `${version}${draft ? " (draft, pending client signature)" : ""}`]),
    rich([{ text: "Effective: ", bold: true }, effective]),
    rich([{ text: "Prepared by: ", bold: true }, `${advisorName}, Wealth Advisor, Contoso Wealth`]),
  ];
  if (draft) {
    children.push(
      rich([{ text: "Status: ", bold: true, color: "C00000" },
        { text: "Draft for review at the year-end planning meeting. Version 1 stays in force until Michael Scott signs this version.", color: "C00000" }]),
      h1("Changes from version 1"),
      table([["Item", "Version 1 (in force)", "Version 2 draft", "Reason"], ...changes], [1900, 2200, 2200, 3060]),
    );
  }
  children.push(
    h1("1. Purpose"),
    p("This statement records Michael Scott's investment objectives, risk tolerance and constraints, and the rules Contoso Wealth follows when managing his portfolio. It guides rebalancing decisions and is reviewed at least once a year or after a material change in circumstances."),
    h1("2. Objectives and risk profile"),
    bullet("Risk tolerance: Moderate."),
    bullet("Objective: long-term growth with moderate volatility; target equity allocation 60% of total portfolio value."),
    bullet("Time horizon: more than 10 years for invested assets, apart from planned large purchases recorded in client meetings."),
    h1("3. Limits and constraints"),
    table([
      ["Constraint", "Limit", "How it is measured"],
      ["Single-stock limit", "25% of total portfolio", "Market value of one individual equity divided by total portfolio value, including cash."],
      ["Sector limit", "40% of total portfolio", "Combined weight of individual equities in one sector (funds excluded)."],
      ["Annual realized-gain tax budget", usd(taxBudget), "Net realized capital gains in a calendar year from advisor-initiated trades."],
      ["Liquidity reserve", usd(story.liquidity_reserve_usd), "Cash and cash equivalents (cash, money market, Treasury bill ETFs) held at all times."],
      ["Income requirement", "None", "No scheduled withdrawals."],
      ["ESG preference", esg, "Applied to new contributions and rebalancing purchases."],
    ], [2600, 2200, 4560]),
    h1("4. Rebalancing policy"),
    bullet("Positions are monitored daily. When a position or sector exceeds its limit, the advisor reviews it with the client before trading."),
    bullet("Trims that bring a position back to its limit should keep the year's realized gains within the tax budget. Larger realized gains need the client's written approval."),
    bullet("Withdrawals for planned purchases are funded first from cash and equivalents above the liquidity reserve, then from rebalancing sales."),
    h1("5. Review and approval"),
    p(draft ? "Review date: year-end planning meeting, Thursday October 22, 2026." : "Next scheduled review: November 3, 2026."),
    table([["Signed by", "Role", "Date"],
      ["Michael Scott", "Client", draft ? "Pending" : "November 3, 2025"],
      [advisorName, "Wealth Advisor, Contoso Wealth", draft ? "Pending" : "November 3, 2025"]], [3600, 3600, 2160]),
  );
  return makeDocument(`Investment Policy Statement ${version}${draft ? " (draft)" : ""}`, children);
}

const meetingNotes = () => makeDocument("Meeting notes: Q4 portfolio review", [
  new Paragraph({ style: "Title", children: [new TextRun("Meeting notes: Q4 portfolio review")] }),
  rich([{ text: "Client: ", bold: true }, "Michael Scott (C0007)"]),
  rich([{ text: "Date: ", bold: true }, "Friday October 16, 2026, 10:30-11:00 PM ET (Teams meeting, recorded and transcribed)"]),
  rich([{ text: "Attendees: ", bold: true }, `Michael Scott (client), ${advisorName} (Wealth Advisor)`]),
  h1("Discussion"),
  numbered("topics", `Portfolio and NVDA concentration. The portfolio is worth about ${usd(story.total_value_discussion_usd)}. NVDA is ${pct(story.nvda_weight_discussion_pct)} of the portfolio, close to the 25% single-stock limit, after its run this year (closed at $${story.nvda_price_discussion.toFixed(2)} today). Information Technology is ${pct(story.it_sector_weight_discussion_pct)} against the 40% sector limit. Agreed: if NVDA goes above 25%, ${advisorFirst} proposes a trim back to the limit, staged so realized gains stay inside the tax budget.`),
  numbered("topics", "Home purchase. Michael and his partner plan to buy a home in spring 2027 and expect a down payment of about $400,000. The $50,000 liquidity reserve stays untouched."),
  numbered("topics", "ESG tilt. Michael wants new contributions to tilt toward ESG funds. The IPS should record this preference."),
  numbered("topics", `Tax budget. The IPS budget for realized gains is ${usd(story.tax_budget_ips_v1_usd)} a year. Michael's accountant may recommend lowering it to $15,000 for 2026. Michael will confirm.`),
  h1("Decisions"),
  bullet("No trades today. NVDA stays under review against the 25% limit."),
  bullet("Prepare IPS version 2 with the ESG preference and the tax budget question for Michael's review."),
  h1("Action items"),
  numbered("actions", `${advisorFirst}: draft IPS v2 (ESG tilt, tax budget review) by Monday October 19.`),
  numbered("actions", `${advisorFirst}: watch the NVDA weight against the 25% limit and flag a breach before the next meeting.`),
  numbered("actions", `${advisorFirst}: outline how to fund the $400,000 down payment without breaching the tax budget.`),
  numbered("actions", "Michael: confirm the home purchase timeline and the 2026 tax budget with his accountant."),
  h1("Next meeting"),
  p("Year-end planning, Thursday October 22, 2026, 10:00 AM ET."),
]);

const researchNote = () => makeDocument("Research note: NVDA", [
  new Paragraph({ style: "Title", children: [new TextRun("NVDA: AI infrastructure demand update")] }),
  rich([{ text: "Contoso Wealth Research  |  ", bold: true }, `${analystName}, Research Analyst  |  October 14, 2026`]),
  h1("Summary"),
  p("Hyperscaler capital spending plans for 2027 point to continued demand for AI accelerators. NVDA has led the large-cap technology rally this year, and many client portfolios now hold it well above their original target weights."),
  h1("Key points"),
  bullet("Data center revenue remains the main driver. The next guidance update can move the stock sharply in either direction."),
  bullet("Valuation is above its five-year average, so position sizing matters more than the long-term view."),
  bullet("A 10% move in NVDA changes the weight of a 24% position by about two percentage points."),
  h1("Concentration watch for advisors"),
  p("Review clients whose NVDA weight is above 20% against their single-stock and sector limits. A position near a 25% limit can breach it after one strong session."),
  table([["NVDA weight before the move", "After a +10% move", "After a +15% move"],
    ["20.0%", "21.6%", "22.3%"], ["24.0%", "25.8%", "26.6%"], ["28.0%", "30.0%", "30.9%"]], [3120, 3120, 3120]),
  p("Weights assume the rest of the portfolio is unchanged. Consider the tax impact and client liquidity needs before trimming.", { italics: true }),
]);

const outputs = [
  ["IPS v1.docx", ips({ version: "v1", draft: false, effective: "November 3, 2025", taxBudget: story.tax_budget_ips_v1_usd, esg: "No preference recorded" })],
  ["IPS v2 DRAFT.docx", ips({
    version: "v2", draft: true, effective: "On client signature (proposed October 22, 2026)", taxBudget: 15000,
    esg: "Prefers ESG tilt for new contributions",
    changes: [
      ["Realized-gain tax budget", usd(story.tax_budget_ips_v1_usd), usd(15000), "Accountant's 2026 recommendation (client to confirm)"],
      ["ESG preference", "No preference recorded", "Prefers ESG tilt", "Client request at the October 16 meeting"],
    ],
  })],
  ["Meeting notes 2026-10-16 Q4 review.docx", meetingNotes()],
  ["Research note - NVDA AI infrastructure update - 2026-10-14.docx", researchNote()],
];
Promise.all(outputs.map(async ([original, doc]) => {
  const name = shiftText(original);
  fs.writeFileSync(path.join(outDir, name), await Packer.toBuffer(doc));
  console.log(`wrote ${name}`);
})).catch((err) => { console.error(err); process.exit(1); });
