// Builds the Contoso Wealth firm library for Foundry IQ: six short policy and research PDFs (fictional demo content),
// plus firm-library.json with one chunk per section (document, section, page, text) for the search index.
// Usage: node build-firm-library.cjs <output-dir>
// Requires pdfkit (NODE_PATH may point at a work folder's node_modules). Dates follow STORY_MEETING_DATE.
"use strict";
const fs = require("fs");
const path = require("path");
const PDFDocument = require("pdfkit");
const { shiftText } = require("./story-dates.cjs");

for (const method of ["text", "heightOfString"]) {
  const original = PDFDocument.prototype[method];
  PDFDocument.prototype[method] = function (text, ...rest) {
    return original.call(this, typeof text === "string" ? shiftText(text) : text, ...rest);
  };
}

const [outDir] = process.argv.slice(2);
if (!outDir) {
  console.error("Usage: node build-firm-library.cjs <outDir>");
  process.exit(2);
}
fs.mkdirSync(outDir, { recursive: true });

const INK = "#262626", MUTED = "#6E6E6E", ACCENT = "#1F3864", RULE = "#D9D9D9";
const PAGE_W = 612, MARGIN = 64, CONTENT_W = PAGE_W - 2 * MARGIN;
const FOOTER = "Contoso Wealth. Fictional demo content for a sample application; not investment, tax or legal advice.";

const chunks = [];
const cellText = (rows) => rows.slice(1).map((cells) => cells.map((c, i) => `${rows[0][i]}: ${c}`).join("; ")).join("\n");

function render({ file, code, title, effective, owner, sections }) {
  const doc = new PDFDocument({
    size: "LETTER", bufferPages: true, margins: { top: 78, bottom: 72, left: MARGIN, right: MARGIN },
    info: { Title: shiftText(`${code} ${title}`), Author: "Contoso Wealth", Subject: shiftText(title),
            Keywords: "Contoso Wealth, firm library, policy, procedure, fictional" },
  });
  const target = path.join(outDir, shiftText(file));
  doc.pipe(fs.createWriteStream(target));
  doc.font("Helvetica-Bold").fontSize(9).fillColor(MUTED).text(`CONTOSO WEALTH  |  ${code}`, MARGIN, 60);
  doc.moveDown(0.6).font("Helvetica-Bold").fontSize(20).fillColor(ACCENT).text(title, { width: CONTENT_W });
  doc.moveDown(0.3).font("Helvetica").fontSize(9.5).fillColor(MUTED)
    .text(`Document ${code}  |  Effective ${effective}  |  Owner: ${owner}`, { width: CONTENT_W });
  doc.moveDown(0.8);
  for (const [heading, body] of sections) {
    const range = doc.bufferedPageRange();
    chunks.push({
      id: `${code}-${chunks.filter((c) => c.doc_code === code).length + 1}`.replace(/[^A-Za-z0-9_-]/g, "-"),
      doc_code: code, title: shiftText(title), section: shiftText(heading), effective: shiftText(effective), owner,
      source_file: shiftText(file), page: range.start + range.count,
      content: shiftText([`${code} ${title}. ${heading}`, ...body.map((item) => (Array.isArray(item) ? cellText(item) : item))].join("\n")),
    });
    doc.font("Helvetica-Bold").fontSize(12.5).fillColor(INK).text(heading, MARGIN, doc.y, { width: CONTENT_W });
    doc.moveDown(0.25);
    for (const item of body) {
      if (Array.isArray(item)) {
        table(doc, item);
        continue;
      }
      const bullet = item.startsWith("- ");
      doc.font("Helvetica").fontSize(10.5).fillColor(INK)
        .text(bullet ? `\u2022  ${item.slice(2)}` : item, MARGIN + (bullet ? 12 : 0), doc.y,
          { width: CONTENT_W - (bullet ? 12 : 0), lineGap: 2.2 });
      doc.moveDown(0.35);
    }
    doc.moveDown(0.4);
  }
  const range = doc.bufferedPageRange();
  for (let i = 0; i < range.count; i++) {
    doc.switchToPage(i);
    const bottom = doc.page.margins.bottom;
    doc.page.margins.bottom = 0;
    doc.font("Helvetica").fontSize(8).fillColor(MUTED)
      .text(FOOTER, MARGIN, 750, { width: CONTENT_W - 70, lineBreak: false })
      .text(`Page ${i + 1} of ${range.count}`, MARGIN, 750, { width: CONTENT_W, align: "right", lineBreak: false });
    doc.page.margins.bottom = bottom;
  }
  doc.end();
  return new Promise((resolve) => doc.on("end", () => resolve(target)));
}

function table(doc, rows) {
  const widths = rows[0].map((_, i) => (i === 0 ? 150 : (CONTENT_W - 150) / (rows[0].length - 1)));
  let y = doc.y + 2;
  rows.forEach((cells, r) => {
    const font = r === 0 ? "Helvetica-Bold" : "Helvetica";
    const h = Math.max(...cells.map((c, i) => doc.font(font).fontSize(9.5).heightOfString(String(c), { width: widths[i] - 10 }))) + 10;
    if (r === 0) doc.rect(MARGIN, y, CONTENT_W, h).fill("#EAF1FB");
    let x = MARGIN;
    cells.forEach((c, i) => {
      doc.font(font).fontSize(9.5).fillColor(INK).text(String(c), x + 5, y + 5, { width: widths[i] - 10 });
      x += widths[i];
    });
    y += h;
    doc.moveTo(MARGIN, y).lineTo(MARGIN + CONTENT_W, y).lineWidth(0.6).strokeColor(RULE).stroke();
  });
  doc.x = MARGIN;
  doc.y = y + 8;
}

const library = [
  {
    file: "CP-104 Concentrated Position Policy.pdf", code: "CP-104", title: "Concentrated Position Policy",
    effective: "January 5, 2026", owner: "Chief Investment Office and Compliance",
    sections: [
      ["1. Purpose", ["Concentrated positions raise a client's risk and can conflict with the limits in the client's Investment Policy Statement (IPS). This policy sets out how advisors identify, review and discuss them. It describes options to discuss with clients; it does not recommend any trade."]],
      ["2. Definitions", [
        "- Concentrated position: a single equity whose weight is above the client's single-stock limit in the signed IPS, or above 20% of the portfolio when the IPS sets no limit.",
        "- Weight: market value of the position divided by total portfolio value, including cash, at the latest market close in the portfolio system of record.",
        "- Signed IPS: the most recent IPS version signed by the client. Drafts are not in force.",
      ]],
      ["3. Review triggers and timeline", [
        "3.1 A position that closes above the client's single-stock limit triggers a documented concentration review within five business days of the breach.",
        "3.2 The advisor contacts the client before any trade. In non-discretionary accounts, no trade is placed without the client's approval.",
        "3.3 A position above 35% of the portfolio also requires sign-off by a supervisory principal before the review is closed.",
      ]],
      ["4. Options to discuss with the client", [
        "- Staged trims across tax years, using tax-lot selection with the highest cost basis first, so realized gains stay within the IPS realized-gain budget.",
        "- Gifting appreciated shares, for example to a donor-advised fund, where the client has charitable goals.",
        "- An exchange fund, for eligible clients who accept a multi-year lockup.",
        "- A protective collar or other hedge. Hedging requires an options agreement and approval by the Derivatives Desk.",
        "- No action, with the client's documented acknowledgment of the risk.",
      ]],
      ["5. Tax budget", [
        "5.1 Trims must keep the calendar year's net realized gains within the IPS realized-gain budget unless the client approves a larger amount in writing.",
        "5.2 A change to the budget takes effect only in a signed IPS. An email from the client or the client's accountant starts the change; it does not make it.",
      ]],
      ["6. Documentation", [
        "Record the review, the options discussed and the client's decision in the CRM within two business days of the client meeting, with the market data date used.",
      ]],
    ],
  },
  {
    file: "OPS-220 Large Withdrawal and Wire Procedure.pdf", code: "OPS-220", title: "Large Withdrawal and Wire Procedure",
    effective: "March 2, 2026", owner: "Operations",
    sections: [
      ["1. Scope", ["Applies to client withdrawals and outgoing wires of $250,000 or more, including real estate closings."]],
      ["2. Required before release", [
        "- A letter of authorization signed by the account owner, with the amount, destination and purpose.",
        "- Call-back verification with the client at a phone number already on file. Never use a number supplied in the request itself.",
        "- Dual approval: the advisor and a second approver in Operations.",
        "- For a real estate closing, wire instructions confirmed by phone with the title or escrow company, using a number from an independent source. Instructions that arrive by email alone are never sufficient: changed wire instructions are the most common closing fraud.",
      ]],
      ["3. Timeline", [
        "3.1 Start the request at least three business days before the date the funds are needed.",
        "3.2 Sales that fund a withdrawal settle on the next business day (T+1); allow for settlement before the wire date.",
        "3.3 Same-day wires must be approved by 2:00 PM Eastern Time.",
      ]],
      ["4. Funding order", [
        "4.1 Use cash and cash equivalents above the client's liquidity reserve first.",
        "4.2 Then consider sales of positions that are above their IPS limits, chosen with the client and within the realized-gain budget (see CP-104).",
        "4.3 The liquidity reserve is not used without the client's written direction.",
      ]],
    ],
  },
  {
    file: "COM-310 Client Communications Standards.pdf", code: "COM-310", title: "Client Communications Standards",
    effective: "February 2, 2026", owner: "Compliance",
    sections: [
      ["1. Content", [
        "- No guarantees or promises of performance. Avoid words such as \"guaranteed\", \"safe\" or \"risk-free\".",
        "- Describe ESG funds by their stated objective. Do not call a fund \"green\" or \"sustainable\" beyond what its prospectus says.",
        "- Never include full account numbers or tax identifiers in email.",
        "- Include the standard disclosure: \"Contoso Wealth does not provide tax or legal advice. Please consult your tax adviser.\" when a message touches on taxes.",
      ]],
      ["2. Instructions from clients", [
        "Trade, withdrawal or wire instructions received by email are not executed until confirmed verbally with the client (see OPS-220 for wires).",
      ]],
      ["3. Review and records", [
        "- Drafts prepared with AI assistance are reviewed and approved by the advisor before they are sent.",
        "- Client communications are archived. Use only approved channels; personal messaging apps are not permitted.",
      ]],
    ],
  },
  {
    file: "ESG-201 Approved ESG Fund Shelf.pdf", code: "ESG-201", title: "Approved ESG Fund Shelf",
    effective: "October 1, 2026", owner: "Investment Product Committee",
    sections: [
      ["1. How to use this shelf", [
        "When a signed IPS records an ESG preference, new contributions and rebalancing purchases use funds from this shelf where they fit the client's risk profile and asset allocation. Existing holdings are not sold only for ESG reasons unless the client asks.",
        "All funds are fictional and for demo purposes only.",
      ]],
      ["2. Approved funds", [[
        ["Fund (ticker)", "Asset class", "Objective", "Expense ratio"],
        ["Contoso US ESG Leaders Fund (CUESG)", "US large-cap equity", "Track an ESG-screened US large-cap index", "0.12%"],
        ["Contoso Global Sustainable Equity Fund (CGSE)", "Global equity", "Companies with leading sustainability scores", "0.25%"],
        ["Contoso Low-Carbon Transition Index Fund (CLCT)", "Global equity", "Lower carbon intensity than the broad market", "0.15%"],
        ["Contoso Green Bond Fund (CGBF)", "Investment-grade bonds", "Bonds that finance environmental projects", "0.20%"],
      ]]],
      ["3. Suitability", [
        "Check each purchase against the client's risk profile, asset allocation and single-stock and sector limits. The ESG tilt does not change those limits.",
      ]],
    ],
  },
  {
    file: "EDU-115 Education Savings 529 Guide.pdf", code: "EDU-115", title: "Education Savings (529) Plan Guide",
    effective: "January 12, 2026", owner: "Wealth Planning",
    sections: [
      ["1. What a 529 plan is", [
        "A 529 plan is a tax-advantaged education savings account. The account owner keeps control of the money and can change the beneficiary to another eligible family member.",
      ]],
      ["2. Before opening or funding one", [
        "- Contributions are gifts to the beneficiary. Large contributions can use five-year gift-tax averaging; the client confirms amounts and any filing with their tax adviser.",
        "- Fund from cash above the liquidity reserve. Do not fund a 529 if it would draw on the liquidity reserve or on money set aside for a known near-term need, such as a home purchase or a tax payment.",
        "- State tax treatment varies by state and plan.",
      ]],
      ["3. Paperwork", [
        "The account owner signs the plan application. A contribution of $250,000 or more follows OPS-220.",
      ]],
    ],
  },
  {
    file: "IC-HV-2026-10 House View AI Semiconductors.pdf", code: "IC-HV-2026-10", title: "Investment Committee House View: AI Semiconductors",
    effective: "October 9, 2026", owner: "Investment Committee",
    sections: [
      ["1. View", [
        "Neutral weight on semiconductors after the sector's strong run this year. Long-term demand for AI data center infrastructure remains intact, but near-term results are sensitive to hyperscaler capital spending guidance.",
      ]],
      ["2. Risks we watch", [
        "- Valuation above the five-year average, so position sizing matters more than the long-term view.",
        "- Customer concentration among a few hyperscale buyers.",
        "- Export controls and supply constraints.",
        "- Power and grid capacity limits for new AI data centers.",
      ]],
      ["3. Guidance for advisors", [
        "For clients above their single-stock limit in AI semiconductor names, hold the concentration review required by CP-104 and discuss staged, tax-aware trims. Do not present this view as a recommendation to an individual client without a suitability review.",
      ]],
    ],
  },
];

Promise.all(library.map(render)).then((files) => {
  for (const file of files) console.log(`wrote ${path.basename(file)}`);
  fs.writeFileSync(path.join(outDir, "firm-library.json"), JSON.stringify(chunks, null, 1));
  console.log(`wrote firm-library.json (${chunks.length} sections)`);
}).catch((err) => { console.error(err); process.exit(1); });
