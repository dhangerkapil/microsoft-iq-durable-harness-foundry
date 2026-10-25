// Builds the advisor's OneDrive PDFs with charts and tables at fixed pages (synthetic demo data).
// Usage: node build-pdfs.cjs <portfolio-story.json> <portfolio-series.json> <nvda-chart.png> <output-dir> [advisorName]
// Requires pdfkit (NODE_PATH may point at a work folder's node_modules).
//
// Michael Scott - Portfolio Overview (Oct 2026).pdf
//   page 1: key figures and summary
//   page 2: Figure 1 (vector bar chart, text labels) and Table 1 (top holdings vs the 25% limit)
//   page 3: Figure 2 (image-only chart; only its caption is text)
// Michael Scott - IPS v1 (signed).pdf
//   page 2: Table 1 (investment limits); page 3: Table 2 (signatures)
"use strict";
const fs = require("fs");
const path = require("path");
const PDFDocument = require("pdfkit");
const { shiftText } = require("./story-dates.cjs");

// Dates, quarters and file names move with STORY_MEETING_DATE (story-dates.cjs); unset keeps the reference story.
// Only the entry points: pdfkit's line wrapper measures words with widthOfString, which must see the shifted text as is.
for (const method of ["text", "heightOfString"]) {
  const original = PDFDocument.prototype[method];
  PDFDocument.prototype[method] = function (text, ...rest) {
    return original.call(this, typeof text === "string" ? shiftText(text) : text, ...rest);
  };
}

const [storyPath, seriesPath, chartPath, outDir, advisorName = "Alex Morgan"] = process.argv.slice(2);
if (!storyPath || !seriesPath || !chartPath || !outDir) {
  console.error("Usage: node build-pdfs.cjs <story.json> <series.json> <chart.png> <outDir> [advisorName]");
  process.exit(2);
}
const story = JSON.parse(fs.readFileSync(storyPath, "utf8")).michael_scott;
const data = JSON.parse(fs.readFileSync(seriesPath, "utf8"));
fs.mkdirSync(outDir, { recursive: true });

const usd = (n, digits = 2) => "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const pct = (n) => `${Number(n).toFixed(1)}%`;
const INK = "#262626", MUTED = "#6E6E6E", ACCENT = "#005EB8", WARN = "#C55A11", RED = "#C00000", RULE = "#D9D9D9";
const PAGE_W = 612, MARGIN = 60, CONTENT_W = PAGE_W - 2 * MARGIN;

function newDocument(info) {
  const shifted = Object.fromEntries(Object.entries(info).map(([key, value]) => [key, shiftText(value)]));
  return new PDFDocument({ size: "LETTER", bufferPages: true, margins: { top: 72, bottom: 72, left: MARGIN, right: MARGIN }, info: shifted });
}

function finish(doc, file, headerText) {
  const range = doc.bufferedPageRange();
  for (let i = 0; i < range.count; i++) {
    doc.switchToPage(i);
    const bottom = doc.page.margins.bottom;
    doc.page.margins.bottom = 0;
    doc.font("Helvetica").fontSize(8).fillColor(MUTED);
    doc.text(`Contoso Wealth  |  ${headerText}`, MARGIN, 34, { width: CONTENT_W, align: "left", lineBreak: false });
    doc.text("Confidential. Synthetic demo content; not investment, tax or legal advice.", MARGIN, 762, { width: CONTENT_W - 80, lineBreak: false });
    doc.text(`Page ${i + 1} of ${range.count}`, MARGIN, 762, { width: CONTENT_W, align: "right", lineBreak: false });
    doc.page.margins.bottom = bottom;
  }
  doc.end();
  return new Promise((resolve) => doc.on("end", resolve));
}

function heading(doc, text, size = 15) {
  doc.moveDown(0.6).font("Helvetica-Bold").fontSize(size).fillColor(INK).text(text, MARGIN, doc.y, { width: CONTENT_W });
  doc.moveDown(0.3);
}

function paragraph(doc, text, options = {}) {
  doc.font(options.bold ? "Helvetica-Bold" : "Helvetica").fontSize(options.size || 10.5).fillColor(options.color || INK)
    .text(text, MARGIN, doc.y, { width: CONTENT_W, lineGap: 2.5, ...options });
  doc.moveDown(0.5);
}

function caption(doc, text) {
  doc.font("Helvetica-Oblique").fontSize(9.5).fillColor(MUTED).text(text, MARGIN, doc.y, { width: CONTENT_W, lineGap: 1.5 });
  doc.moveDown(0.6);
}

function table(doc, columns, rows, options = {}) {
  const widths = columns.map((c) => c.width);
  const x0 = MARGIN;
  let y = doc.y;
  const rowHeight = (cells, font, size) => Math.max(...cells.map((cell, i) => {
    doc.font(font).fontSize(size);
    return doc.heightOfString(String(cell), { width: widths[i] - 10 }) + 9;
  }));
  const draw = (cells, header, fill) => {
    const font = header ? "Helvetica-Bold" : "Helvetica";
    const size = header ? 9.5 : 9.5;
    const h = rowHeight(cells, font, size);
    if (fill) doc.rect(x0, y, widths.reduce((a, b) => a + b, 0), h).fill(fill);
    let x = x0;
    cells.forEach((cell, i) => {
      doc.font(font).fontSize(size).fillColor(options.colors && !header && options.colors[i] ? options.colors[i](cell) : INK)
        .text(String(cell), x + 5, y + 5, { width: widths[i] - 10, align: columns[i].align || "left" });
      x += widths[i];
    });
    y += h;
    doc.moveTo(x0, y).lineTo(x0 + widths.reduce((a, b) => a + b, 0), y).lineWidth(0.6).strokeColor(RULE).stroke();
  };
  draw(columns.map((c) => c.label), true, "#EAF1FB");
  rows.forEach((row, index) => draw(row, false, index % 2 ? "#FAFAFA" : null));
  doc.x = MARGIN;
  doc.y = y + 8;
}

// --- Portfolio overview ---------------------------------------------------------------------------
async function portfolioOverview() {
  const file = path.join(outDir, shiftText("Michael Scott - Portfolio Overview (Oct 2026).pdf"));
  const doc = newDocument({
    Title: "Michael Scott - Portfolio Overview (October 2026)",
    Author: `${advisorName}, Contoso Wealth`,
    Subject: "Portfolio overview with sector allocation chart, top holdings table and NVDA concentration trend",
    Keywords: "Michael Scott, portfolio overview, sector allocation chart, top holdings table, NVDA concentration, single-stock limit, sector limit",
  });
  const out = fs.createWriteStream(file);
  doc.pipe(out);
  const holdings = data.holdings;
  const weight = (t) => holdings.find((h) => h.ticker === t).weight_pct;
  const value = (t) => holdings.find((h) => h.ticker === t).market_value;
  const cash = value("CASH") + value("SGOV");

  // Page 1: summary
  doc.font("Helvetica-Bold").fontSize(24).fillColor(INK).text("Portfolio Overview: Michael Scott", MARGIN, 80, { width: CONTENT_W });
  doc.moveDown(0.3);
  paragraph(doc, "Client C0007  |  Private Client  |  Moderate risk profile  |  Scranton, PA", { color: MUTED, size: 10 });
  paragraph(doc, `Prepared by ${advisorName}, Wealth Advisor, Contoso Wealth, for the year-end planning meeting on Thursday, October 22, 2026. Positions and prices as of market close on Friday, October 16, 2026.`);
  heading(doc, "Key figures");
  table(doc, [
    { label: "Measure", width: 230 }, { label: "Value", width: 120, align: "right" }, { label: "Policy reference (IPS v1)", width: CONTENT_W - 350 },
  ], [
    ["Total portfolio value", usd(data.total_value), "-"],
    ["Largest position: NVIDIA (NVDA)", pct(weight("NVDA")), "Single-stock limit 25%"],
    ["Information Technology sector", pct(story.it_sector_weight_discussion_pct), "Sector limit 40%"],
    ["Cash and cash equivalents (cash, SGOV)", usd(cash), "Liquidity reserve " + usd(story.liquidity_reserve_usd, 0)],
    ["Annual realized-gain tax budget", usd(story.tax_budget_ips_v1_usd, 0), "IPS v1, signed November 3, 2025"],
  ]);
  heading(doc, "Summary");
  paragraph(doc, `NVDA (${pct(weight("NVDA"))}) and the Information Technology sector (${pct(story.it_sector_weight_discussion_pct)}) are both within one percentage point of their IPS limits. One strong session for NVDA could move the position above the 25% single-stock limit. A trim back to the limit would realize gains that count against the ${usd(story.tax_budget_ips_v1_usd, 0)} annual tax budget, because the NVDA cost basis is ${usd(story.nvda_basis_per_share)} per share.`);
  paragraph(doc, "Planned cash needs: Michael expects to fund a home down payment of about $400,000. Cash needs are covered first from cash and equivalents above the liquidity reserve.");
  heading(doc, "Inside this overview");
  paragraph(doc, "Page 2: allocation by sector and asset class, and the largest holdings against the single-stock limit.");
  paragraph(doc, "Page 3: how the NVDA position has moved against its limit since September.");

  // Page 2: Figure 1 (vector chart) and Table 1
  doc.addPage();
  heading(doc, "Allocation and top holdings", 17);
  const sectors = [
    ["Information Technology (NVDA, MSFT, AAPL)", story.it_sector_weight_discussion_pct, true],
    ["Fixed income (BND, MUB)", weight("BND") + weight("MUB")],
    ["US equity funds (VTI)", weight("VTI")],
    ["Cash and equivalents (cash, SGOV)", weight("CASH") + weight("SGOV")],
    ["Consumer Discretionary (AMZN)", weight("AMZN")],
    ["International equity funds (VXUS)", weight("VXUS")],
    ["Communication Services (GOOGL)", weight("GOOGL")],
    ["Financials (JPM)", weight("JPM")],
    ["Health Care (JNJ)", weight("JNJ")],
  ];
  const chartTop = doc.y + 6, labelW = 205, barX = MARGIN + labelW, barMaxW = CONTENT_W - labelW - 50, barH = 15, gap = 8, scale = barMaxW / 45;
  doc.font("Helvetica-Bold").fontSize(10).fillColor(INK).text("Share of portfolio by sector and asset class", MARGIN, chartTop, { width: CONTENT_W });
  let y = chartTop + 22;
  sectors.forEach(([label, w, highlight]) => {
    doc.font("Helvetica").fontSize(8.8).fillColor(INK).text(label, MARGIN, y + 3, { width: labelW - 8, align: "right" });
    doc.rect(barX, y, w * scale, barH).fill(highlight ? WARN : ACCENT);
    if (highlight) {
      // Keep the label clear of the dashed limit line just past the bar.
      doc.font("Helvetica-Bold").fontSize(8.8).fillColor("#FFFFFF").text(pct(w), barX, y + 3.5, { width: w * scale - 6, align: "right", lineBreak: false });
    } else {
      doc.font("Helvetica-Bold").fontSize(8.8).fillColor(INK).text(pct(w), barX + w * scale + 5, y + 3.5, { lineBreak: false });
    }
    y += barH + gap;
  });
  const limitX = barX + 40 * scale;
  doc.moveTo(limitX, chartTop + 16).lineTo(limitX, y).dash(4, { space: 3 }).lineWidth(1.4).strokeColor(RED).stroke().undash();
  doc.font("Helvetica-Bold").fontSize(8.5).fillColor(RED).text("40% sector limit", limitX - 72, y + 2, { width: 70, align: "right" });
  doc.y = y + 18;
  caption(doc, "Figure 1. Allocation by sector and asset class against the 40% sector limit, as of October 16, 2026. Information Technology (39.1%) is the only sector near its limit.");

  doc.moveDown(0.4);
  doc.font("Helvetica-Bold").fontSize(10.5).fillColor(INK).text("Table 1. Top holdings against the 25% single-stock limit", MARGIN, doc.y, { width: CONTENT_W });
  doc.moveDown(0.3);
  const rows = holdings.slice(0, 8).map((h) => {
    const single = h.asset_class === "Equity";
    const headroom = single ? (25 - h.weight_pct) : null;
    return [h.ticker, h.name, pct(h.weight_pct), usd(h.market_value, 0), single ? "25%" : "n/a (fund or cash)",
      single ? (headroom < 1 ? `${headroom.toFixed(1)} pts - watch` : `${headroom.toFixed(1)} pts`) : "-"];
  });
  table(doc, [
    { label: "Ticker", width: 50 }, { label: "Holding", width: 152 }, { label: "Weight", width: 52, align: "right" },
    { label: "Market value", width: 80, align: "right" }, { label: "Limit", width: 85 }, { label: "Headroom", width: CONTENT_W - 419 },
  ], rows, { colors: [null, null, null, null, null, (cell) => String(cell).includes("watch") ? RED : INK] });

  // Page 3: Figure 2 (image only)
  doc.addPage();
  heading(doc, "NVDA concentration trend", 17);
  paragraph(doc, "The chart below is an image exported from the portfolio system. Its values are not available as document text.", { color: MUTED, size: 9.5 });
  doc.image(chartPath, MARGIN, doc.y + 4, { width: CONTENT_W });
  doc.y += CONTENT_W * (760 / 1600) + 14;
  caption(doc, "Figure 2. NVDA weight against the 25% single-stock limit, September 1 to October 16, 2026 (chart image).");
  heading(doc, "Notes for the review", 12);
  const recent = data.series.filter((p) => p.date >= shiftText("2026-09-01")).map((p) => p.nvda_pct);
  paragraph(doc, `Since September 1, NVDA has ranged between ${Math.min(...recent).toFixed(1)}% and ${Math.max(...recent).toFixed(1)}% of the portfolio, within two percentage points of its limit. Review any trim with the client before trading and test it against the annual tax budget.`);
  paragraph(doc, "Source: Contoso Wealth portfolio system snapshot, October 16, 2026. Weights include cash.", { color: MUTED, size: 9 });

  await finish(doc, file, "Portfolio overview: Michael Scott");
  return file;
}

// --- Signed IPS v1 --------------------------------------------------------------------------------
async function signedIps() {
  const file = path.join(outDir, "Michael Scott - IPS v1 (signed).pdf");
  const doc = newDocument({
    Title: "Michael Scott - Investment Policy Statement v1 (signed)",
    Author: `${advisorName}, Contoso Wealth`,
    Subject: "Signed Investment Policy Statement version 1 with the investment limits table and signature page",
    Keywords: "Michael Scott, IPS, investment policy statement, signed, single-stock limit, sector limit, tax budget, liquidity reserve",
  });
  doc.pipe(fs.createWriteStream(file));
  doc.font("Helvetica-Bold").fontSize(24).fillColor(INK).text("Investment Policy Statement", MARGIN, 80, { width: CONTENT_W });
  paragraph(doc, "Version 1  |  Signed copy", { color: MUTED });
  table(doc, [{ label: "Item", width: 160 }, { label: "Detail", width: CONTENT_W - 160 }], [
    ["Client", "Michael Scott (client C0007), Scranton, PA"],
    ["Advisor", `${advisorName}, Wealth Advisor, Contoso Wealth`],
    ["Effective date", "November 3, 2025"],
    ["Next scheduled review", "November 3, 2026"],
    ["Risk profile", "Moderate; target equity allocation 60%"],
  ]);
  heading(doc, "1. Purpose");
  paragraph(doc, "This statement sets out the objectives, limits and review process the advisor follows when managing Michael Scott's portfolio. It stays in force until a later version is signed by the client.");
  heading(doc, "2. Objectives");
  paragraph(doc, "Long-term growth with moderate risk, broad diversification, and enough cash and equivalents to meet planned needs without forced sales.");

  doc.addPage();
  heading(doc, "3. Limits and constraints", 17);
  doc.font("Helvetica-Bold").fontSize(10.5).fillColor(INK).text("Table 1. Investment limits and constraints", MARGIN, doc.y, { width: CONTENT_W });
  doc.moveDown(0.3);
  table(doc, [{ label: "Constraint", width: 150 }, { label: "Limit", width: 120 }, { label: "How it is measured", width: CONTENT_W - 270 }], [
    ["Single-stock limit", "25% of total portfolio", "Market value of one individual equity divided by total portfolio value, including cash."],
    ["Sector limit", "40% of total portfolio", "Combined weight of individual equities in one sector (funds excluded)."],
    ["Annual realized-gain tax budget", usd(story.tax_budget_ips_v1_usd, 0), "Net realized capital gains in a calendar year from advisor-initiated trades."],
    ["Liquidity reserve", usd(story.liquidity_reserve_usd, 0), "Cash and cash equivalents (cash, money market, Treasury bill ETFs) held at all times."],
  ]);
  heading(doc, "4. Monitoring and rebalancing");
  paragraph(doc, "Positions are monitored daily. When a position or sector exceeds its limit, the advisor reviews it with the client before trading.");
  paragraph(doc, "Trims that bring a position back to its limit should keep the year's realized gains within the tax budget. Larger realized gains need the client's written approval.");
  paragraph(doc, "Withdrawals for planned purchases are funded first from cash and equivalents above the liquidity reserve, then from rebalancing sales.");

  doc.addPage();
  heading(doc, "5. Signatures", 17);
  paragraph(doc, "By signing, the client and the advisor agree to this Investment Policy Statement, version 1.");
  doc.font("Helvetica-Bold").fontSize(10.5).fillColor(INK).text("Table 2. Signatures", MARGIN, doc.y, { width: CONTENT_W });
  doc.moveDown(0.3);
  table(doc, [{ label: "Signed by", width: 150 }, { label: "Role", width: 150 }, { label: "Date", width: 100 }, { label: "Signature", width: CONTENT_W - 400 }], [
    ["Michael Scott", "Client", "November 3, 2025", "/s/ Michael Scott"],
    [advisorName, "Wealth Advisor, Contoso Wealth", "November 3, 2025", `/s/ ${advisorName}`],
  ]);
  paragraph(doc, "Version 1 remains in force until the client signs a later version. Drafts are not binding.", { color: MUTED, size: 9.5 });
  await finish(doc, file, "Investment Policy Statement v1 (signed): Michael Scott");
  return file;
}

(async () => {
  const files = [await portfolioOverview(), await signedIps()];
  for (const f of files) console.log(f);
})().catch((error) => { console.error(error); process.exit(1); });
