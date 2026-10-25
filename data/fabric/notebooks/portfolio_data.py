# %% [markdown]
# # Advisor Harness: synthetic portfolio data
# Builds deterministic, fictional portfolio tables for the Contoso Wealth demo.
# Prices, clients and holdings are synthetic. They are not market data or investment advice.
# Deploy-FabricPortfolio.ps1 replaces the __PLACEHOLDERS__ and runs this notebook.

# %%
import datetime as dt
import json
import math
import random

from pyspark.sql import types as T

WORKSPACE_ID = "__WORKSPACE_ID__"
LAKEHOUSE_ID = "__LAKEHOUSE_ID__"
ONELAKE = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LAKEHOUSE_ID}"
status = {"notebook": "advisor_portfolio_data", "started": dt.datetime.utcnow().isoformat() + "Z", "tables": {}}

# Whole days (a multiple of 7) from the reference story, set from STORY_MEETING_DATE (hydration/story_dates.py).
STORY_SHIFT_DAYS = "__STORY_SHIFT_DAYS__"
SHIFT = dt.timedelta(days=int(STORY_SHIFT_DAYS) if STORY_SHIFT_DAYS.lstrip("-").isdigit() else 0)


def story_date(year, month, day):
    return dt.date(year, month, day) + SHIFT


START = story_date(2026, 6, 30)
DISCUSSION = story_date(2026, 10, 16)  # last client discussion in the demo story
AS_OF = story_date(2026, 10, 19)  # latest close available on demo day
DAYS = [d for d in (START + dt.timedelta(days=n) for n in range((AS_OF - START).days + 1)) if d.weekday() < 5]
assert DISCUSSION in DAYS and AS_OF in DAYS
CHANGE_PAIRS = [(DISCUSSION, AS_OF), (story_date(2026, 9, 30), AS_OF), (START, AS_OF)]

# ticker, name, sector, asset class, ESG rating, start price, annual volatility, annual drift
SECURITIES = [
    ("NVDA", "NVIDIA Corp.", "Information Technology", "Equity", "B", 158.20, 0.45, 0.30),
    ("MSFT", "Microsoft Corp.", "Information Technology", "Equity", "A", 471.30, 0.22, 0.12),
    ("AAPL", "Apple Inc.", "Information Technology", "Equity", "B", 214.70, 0.25, 0.10),
    ("AMZN", "Amazon.com Inc.", "Consumer Discretionary", "Equity", "B", 209.40, 0.30, 0.12),
    ("GOOGL", "Alphabet Inc. Class A", "Communication Services", "Equity", "B", 179.90, 0.28, 0.10),
    ("META", "Meta Platforms Inc.", "Communication Services", "Equity", "C", 688.50, 0.35, 0.14),
    ("JPM", "JPMorgan Chase & Co.", "Financials", "Equity", "B", 284.60, 0.22, 0.08),
    ("JNJ", "Johnson & Johnson", "Health Care", "Equity", "A", 164.80, 0.16, 0.04),
    ("UNH", "UnitedHealth Group Inc.", "Health Care", "Equity", "B", 318.20, 0.28, 0.03),
    ("XOM", "Exxon Mobil Corp.", "Energy", "Equity", "C", 111.90, 0.24, 0.02),
    ("NEE", "NextEra Energy Inc.", "Utilities", "Equity", "A", 73.60, 0.22, 0.05),
    ("PG", "Procter & Gamble Co.", "Consumer Staples", "Equity", "A", 159.30, 0.15, 0.04),
    ("KO", "Coca-Cola Co.", "Consumer Staples", "Equity", "A", 70.10, 0.14, 0.04),
    ("VTI", "Vanguard Total Stock Market ETF", "Diversified US Equity", "ETF", "B", 301.20, 0.16, 0.08),
    ("VXUS", "Vanguard Total International Stock ETF", "International Equity", "ETF", "B", 68.40, 0.15, 0.06),
    ("ESGU", "iShares ESG Aware MSCI USA ETF", "Diversified US Equity", "ETF", "A", 134.70, 0.16, 0.08),
    ("BND", "Vanguard Total Bond Market ETF", "Fixed Income", "ETF", "A", 72.90, 0.05, 0.02),
    ("MUB", "iShares National Muni Bond ETF", "Fixed Income", "ETF", "A", 105.80, 0.04, 0.02),
    ("SGOV", "iShares 0-3 Month Treasury Bond ETF", "Cash Equivalents", "ETF", "A", 100.52, 0.003, 0.045),
    ("CASH", "US dollar cash", "Cash", "Cash", "A", 1.00, 0.0, 0.0),
]
SEC = {s[0]: s for s in SECURITIES}


def price_path(ticker, start, vol, drift):
    if ticker == "CASH":
        return {d: 1.0 for d in DAYS}
    rng = random.Random(f"price-{ticker}")
    price, path = start, {}
    for i, day in enumerate(DAYS):
        if i:
            price *= math.exp((drift - 0.5 * vol * vol) / 252 + vol / math.sqrt(252) * rng.gauss(0, 1))
        path[day] = price
    return path


PRICES = {s[0]: price_path(s[0], s[5], s[6], s[7]) for s in SECURITIES}
# Story: NVDA closes at 182.40 on the discussion day, then rallies 11.2% on the next trading day.
anchor = 182.40
factor = anchor / PRICES["NVDA"][DISCUSSION]
for day in DAYS:
    if day <= DISCUSSION:
        PRICES["NVDA"][day] *= factor
PRICES["NVDA"][AS_OF] = anchor * 1.112
for ticker in PRICES:
    if ticker not in ("NVDA", "CASH"):
        PRICES[ticker][AS_OF] = PRICES[ticker][DISCUSSION] * (1 + random.Random(f"move-{ticker}").uniform(-0.006, 0.008))
PRICES = {t: {d: round(p, 2) for d, p in path.items()} for t, path in PRICES.items()}

# %%
# client id, name, segment, risk profile, city, state, client since, portfolio value on the discussion day
CLIENTS = [
    ("C0001", "Jan Levinson", "Private Client", "Aggressive", "Nashua", "NH", dt.date(2016, 4, 11), 4_200_000),
    ("C0002", "David Wallace", "Ultra High Net Worth", "Moderate", "New York", "NY", dt.date(2011, 9, 6), 12_500_000),
    ("C0003", "Robert California", "Ultra High Net Worth", "Aggressive", "Tallahassee", "FL", dt.date(2018, 1, 22), 9_800_000),
    ("C0004", "Jo Bennett", "Ultra High Net Worth", "Moderate", "Tallahassee", "FL", dt.date(2013, 6, 17), 7_400_000),
    ("C0005", "Holly Flax", "Private Client", "Conservative", "Nashua", "NH", dt.date(2019, 2, 4), 1_150_000),
    ("C0006", "Toby Flenderson", "Private Client", "Conservative", "Scranton", "PA", dt.date(2015, 8, 3), 980_000),
    ("C0007", "Michael Scott", "Private Client", "Moderate", "Scranton", "PA", dt.date(2014, 3, 3), 2_850_000),
    ("C0008", "Pam Beesly", "Private Client", "Conservative", "Scranton", "PA", dt.date(2017, 5, 15), 640_000),
    ("C0009", "Jim Halpert", "Private Client", "Moderate", "Philadelphia", "PA", dt.date(2017, 5, 15), 1_320_000),
    ("C0010", "Dwight Schrute", "Private Client", "Aggressive", "Honesdale", "PA", dt.date(2012, 10, 1), 3_600_000),
    ("C0011", "Stanley Hudson", "Private Client", "Conservative", "Scranton", "PA", dt.date(2010, 7, 12), 1_900_000),
    ("C0012", "Phyllis Vance", "Private Client", "Moderate", "Scranton", "PA", dt.date(2014, 11, 10), 1_050_000),
    ("C0013", "Oscar Martinez", "Private Client", "Moderate", "Scranton", "PA", dt.date(2016, 3, 7), 890_000),
    ("C0014", "Angela Martin", "Private Client", "Conservative", "Scranton", "PA", dt.date(2015, 1, 19), 1_480_000),
    ("C0015", "Kevin Malone", "Private Client", "Aggressive", "Scranton", "PA", dt.date(2020, 9, 14), 410_000),
]
CLIENT = {c[0]: c for c in CLIENTS}
RISK_LIMITS = {  # target equity %, single-stock limit %, sector limit %
    "Conservative": (35.0, 15.0, 30.0),
    "Moderate": (60.0, 25.0, 40.0),
    "Aggressive": (80.0, 25.0, 45.0),
}
TEMPLATES = {
    "Conservative": {"VTI": 20, "VXUS": 8, "BND": 30, "MUB": 20, "SGOV": 8, "CASH": 4, "JNJ": 3, "PG": 3, "KO": 2, "MSFT": 2},
    "Moderate": {"VTI": 25, "VXUS": 10, "ESGU": 8, "BND": 18, "MUB": 10, "SGOV": 4, "CASH": 3, "MSFT": 5, "AAPL": 4,
                 "AMZN": 3, "JPM": 3, "UNH": 3, "NEE": 2, "PG": 2},
    "Aggressive": {"VTI": 25, "VXUS": 10, "NVDA": 8, "MSFT": 8, "AAPL": 7, "AMZN": 7, "GOOGL": 6, "META": 6, "JPM": 5,
                   "XOM": 3, "BND": 8, "SGOV": 3, "CASH": 4},
}
# Michael Scott's exact allocation on the discussion day: NVDA 24.1% against a 25% single-stock limit.
FIXED_WEIGHTS = {
    "C0007": {"NVDA": 24.1, "MSFT": 8.5, "AAPL": 6.5, "AMZN": 5.5, "GOOGL": 4.5, "JPM": 4.0, "JNJ": 3.0,
              "VTI": 12.0, "VXUS": 5.0, "BND": 9.0, "MUB": 8.0, "SGOV": 3.9, "CASH": 6.0},
}
OVERRIDES = {"C0010": {"META": 27.0}}  # a second client already above the single-stock limit


def weights_for(client_id, risk):
    if client_id in FIXED_WEIGHTS:
        return dict(FIXED_WEIGHTS[client_id])
    rng = random.Random(f"weights-{client_id}")
    raw = {t: v * rng.uniform(0.85, 1.15) for t, v in TEMPLATES[risk].items()}
    fixed = OVERRIDES.get(client_id, {})
    scale = (100 - sum(fixed.values())) / sum(v for t, v in raw.items() if t not in fixed)
    return {t: fixed.get(t, v * scale) for t, v in raw.items()}


BASIS = {("C0007", "NVDA"): 41.20, ("C0007", "MSFT"): 292.10, ("C0007", "AAPL"): 148.55}


def basis_per_share(client_id, ticker, price):
    if ticker == "CASH":
        return 1.0
    if (client_id, ticker) in BASIS:
        return BASIS[(client_id, ticker)]
    return round(price * random.Random(f"basis-{client_id}-{ticker}").uniform(0.58, 1.04), 2)


# trade date, client id, ticker, action, quantity, note; cash effect is derived from prices
TRADES = [
    (story_date(2026, 8, 20), "C0007", "VTI", "BUY", 100, "Rebalance into broad US equity"),
    (story_date(2026, 9, 15), "C0007", "MSFT", "DIVIDEND", None, "Quarterly dividend"),
    (story_date(2026, 10, 1), "C0007", "CASH", "DEPOSIT", None, "Quarterly bonus deposit"),
    (story_date(2026, 9, 30), "C0010", "META", "BUY", 150, "Client-directed purchase"),
    (story_date(2026, 9, 15), "C0002", "MSFT", "DIVIDEND", None, "Quarterly dividend"),
    (story_date(2026, 9, 12), "C0011", "JNJ", "DIVIDEND", None, "Quarterly dividend"),
    (story_date(2026, 8, 3), "C0008", "CASH", "WITHDRAWAL", None, "Planned distribution"),
]
CASH_AMOUNTS = {("C0007", "MSFT", "DIVIDEND"): 1870.40, ("C0007", "CASH", "DEPOSIT"): 25000.00,
                ("C0002", "MSFT", "DIVIDEND"): 9410.25, ("C0011", "JNJ", "DIVIDEND"): 2145.60,
                ("C0008", "CASH", "WITHDRAWAL"): -6000.00}

# Base quantities as of the discussion day.
BASE, WEIGHTS = {}, {}
for cid, name, segment, risk, city, state, since, value in CLIENTS:
    WEIGHTS[cid] = weights_for(cid, risk)
    BASE[cid] = {}
    for ticker, weight in WEIGHTS[cid].items():
        price = PRICES[ticker][DISCUSSION]
        quantity = weight / 100 * value / price
        BASE[cid][ticker] = round(quantity, 2) if ticker == "CASH" else float(round(quantity))

TRANSACTIONS = []  # rows for the transactions table, with quantity and cash effects
for day, cid, ticker, action, quantity, note in TRADES:
    if action == "BUY":
        price = PRICES[ticker][day]
        cash = -round(quantity * price, 2)
    else:
        price, cash = None, CASH_AMOUNTS[(cid, ticker, action)]
    TRANSACTIONS.append((day, cid, ticker, action, None if quantity is None else float(quantity), price, cash, note))


def positions_on(cid, day):
    """Quantities on a day: base holdings with later transactions undone."""
    held = dict(BASE[cid])
    for t_day, t_cid, ticker, action, quantity, price, cash, note in TRANSACTIONS:
        if t_cid == cid and t_day > day:
            if action == "BUY":
                held[ticker] = held.get(ticker, 0.0) - quantity
            held["CASH"] = round(held.get("CASH", 0.0) - cash, 2)
    return held

# %%
POSITIONS, SUMMARY = [], []
SNAP = {}  # (client, day) -> {ticker: (quantity, market value, weight)}
for day in DAYS:
    for cid, name, segment, risk, city, state, since, value in CLIENTS:
        target_equity, single_limit, sector_limit = RISK_LIMITS[risk]
        held = positions_on(cid, day)
        mv = {t: q * PRICES[t][day] for t, q in held.items()}
        total = sum(mv.values())
        weights = {t: v / total * 100 for t, v in mv.items()}
        sectors = {}
        for t, w in weights.items():
            if SEC[t][3] == "Equity":
                sectors[SEC[t][2]] = sectors.get(SEC[t][2], 0.0) + w
        SNAP[(cid, day)] = {t: (held[t], mv[t], weights[t]) for t in held}
        unrealized_total, over_single = 0.0, 0
        for t, q in sorted(held.items()):
            basis = round(q * basis_per_share(cid, t, PRICES[t][DISCUSSION]), 2)
            gain = round(mv[t] - basis, 2)
            unrealized_total += gain
            equity = SEC[t][3] == "Equity"
            over = equity and weights[t] > single_limit
            over_single += int(over)
            sector_w = round(sectors[SEC[t][2]], 2) if equity else None
            POSITIONS.append((day, cid, t, SEC[t][1], SEC[t][2], SEC[t][3], float(q), float(PRICES[t][day]),
                              round(mv[t], 2), round(weights[t], 2), basis, gain,
                              single_limit if equity else None, bool(over), sector_w,
                              sector_limit if equity else None, bool(equity and sector_w > sector_limit)))
        equities = {t: w for t, w in weights.items() if SEC[t][3] == "Equity"}
        top = max(equities, key=equities.get) if equities else None
        SUMMARY.append((day, cid, name, round(total, 2), round(mv.get("CASH", 0.0) + mv.get("SGOV", 0.0), 2),
                        round(unrealized_total, 2), top, round(equities[top], 2) if top else None, over_single,
                        sum(1 for w in sectors.values() if w > sector_limit)))

CHANGES = []
for from_day, to_day in CHANGE_PAIRS:
    for cid in CLIENT:
        before, after = SNAP[(cid, from_day)], SNAP[(cid, to_day)]
        for t in sorted(set(before) | set(after)):
            q0, v0, w0 = before.get(t, (0.0, 0.0, 0.0))
            q1, v1, w1 = after.get(t, (0.0, 0.0, 0.0))
            p0, p1 = PRICES[t][from_day], PRICES[t][to_day]
            CHANGES.append((cid, t, from_day, to_day, round(w0, 2), round(w1, 2), round(w1 - w0, 2), round(v0, 2),
                            round(v1, 2), round(v1 - v0, 2), p0, p1, round((p1 / p0 - 1) * 100, 2)))

IPS = []
for cid, name, segment, risk, city, state, since, value in CLIENTS:
    target_equity, single_limit, sector_limit = RISK_LIMITS[risk]
    rng = random.Random(f"ips-{cid}")
    if cid == "C0007":
        IPS.append((cid, risk, target_equity, single_limit, sector_limit, 20000.0, 50000.0, 0.0,
                    "No preference recorded", "v1", story_date(2025, 11, 3), story_date(2026, 11, 3),
                    "Client Files/Michael Scott/IPS v1.docx"))
        continue
    IPS.append((cid, risk, target_equity, single_limit, sector_limit, float(rng.choice([10000, 15000, 25000, 40000])),
                float(round(value * rng.uniform(0.02, 0.05), -3)),
                float(round(value * 0.03, -3)) if risk == "Conservative" else 0.0,
                rng.choice(["No preference recorded", "Prefers ESG tilt", "Exclude fossil fuels"]), "v1",
                story_date(2025, rng.randint(1, 12), rng.randint(1, 28)), story_date(2026, rng.randint(11, 12), 15),
                f"Client Files/{name}/IPS v1.docx"))

PRICE_ROWS = [(d, t, float(PRICES[t][d])) for t in PRICES for d in DAYS]
CLIENT_ROWS = [(c[0], c[1], c[2], c[3], c[4], c[5], c[6], f"Contoso Wealth - {c[4]}") for c in CLIENTS]
SECURITY_ROWS = [(s[0], s[1], s[2], s[3], s[4]) for s in SECURITIES]

# %%
S, D, DT, B, I = T.StringType(), T.DoubleType(), T.DateType(), T.BooleanType(), T.IntegerType()


def schema(*fields):
    return T.StructType([T.StructField(name, kind, True) for name, kind in fields])


TABLES = {
    "clients": (CLIENT_ROWS, schema(("client_id", S), ("client_name", S), ("segment", S), ("risk_profile", S),
                                    ("city", S), ("state", S), ("client_since", DT), ("relationship_team", S))),
    "ips_profiles": (IPS, schema(("client_id", S), ("risk_tolerance", S), ("target_equity_pct", D),
                                 ("single_stock_limit_pct", D), ("sector_limit_pct", D), ("annual_tax_budget_usd", D),
                                 ("liquidity_reserve_usd", D), ("annual_income_need_usd", D), ("esg_preference", S),
                                 ("ips_version", S), ("ips_effective_date", DT), ("next_review_date", DT),
                                 ("ips_document", S))),
    "securities": (SECURITY_ROWS, schema(("ticker", S), ("security_name", S), ("sector", S), ("asset_class", S),
                                         ("esg_rating", S))),
    "prices_daily": (PRICE_ROWS, schema(("as_of_date", DT), ("ticker", S), ("close_price", D))),
    "positions_daily": (POSITIONS, schema(("as_of_date", DT), ("client_id", S), ("ticker", S), ("security_name", S),
                                          ("sector", S), ("asset_class", S), ("quantity", D), ("close_price", D),
                                          ("market_value_usd", D), ("weight_pct", D), ("cost_basis_usd", D),
                                          ("unrealized_gain_usd", D), ("single_stock_limit_pct", D),
                                          ("over_single_stock_limit", B), ("sector_weight_pct", D),
                                          ("sector_limit_pct", D), ("over_sector_limit", B))),
    "position_changes": (CHANGES, schema(("client_id", S), ("ticker", S), ("from_date", DT), ("to_date", DT),
                                         ("weight_from_pct", D), ("weight_to_pct", D), ("weight_change_pts", D),
                                         ("value_from_usd", D), ("value_to_usd", D), ("value_change_usd", D),
                                         ("price_from", D), ("price_to", D), ("price_change_pct", D))),
    "client_summary_daily": (SUMMARY, schema(("as_of_date", DT), ("client_id", S), ("client_name", S),
                                             ("total_value_usd", D), ("cash_and_equivalents_usd", D),
                                             ("unrealized_gain_usd", D), ("largest_equity_ticker", S),
                                             ("largest_equity_weight_pct", D), ("positions_over_single_stock_limit", I),
                                             ("sectors_over_limit", I))),
    "transactions": (TRANSACTIONS, schema(("trade_date", DT), ("client_id", S), ("ticker", S), ("action", S),
                                          ("quantity", D), ("price", D), ("cash_amount_usd", D), ("note", S))),
}

for table, (rows, table_schema) in TABLES.items():
    frame = spark.createDataFrame(rows, schema=table_schema)
    (frame.write.format("delta").mode("overwrite").option("overwriteSchema", "true")
     .save(f"{ONELAKE}/Tables/{table}"))
    status["tables"][table] = spark.read.format("delta").load(f"{ONELAKE}/Tables/{table}").count()
print(status["tables"])

# %%
# Story numbers reused by the Microsoft 365 content, the runbook and the evaluation ground truth.
m_disc, m_now = SNAP[("C0007", DISCUSSION)], SNAP[("C0007", AS_OF)]


def sector_weight(snapshot, sector):
    return round(sum(w for t, (q, v, w) in snapshot.items() if SEC[t][2] == sector and SEC[t][3] == "Equity"), 2)


total_now = sum(v for q, v, w in m_now.values())
nvda_q, nvda_v, nvda_w = m_now["NVDA"]
trim_usd = max(0.0, nvda_v - 0.25 * total_now)
nvda_price = PRICES["NVDA"][AS_OF]
status["michael_scott"] = {
    "client_id": "C0007", "discussion_date": str(DISCUSSION), "as_of_date": str(AS_OF),
    "nvda_weight_discussion_pct": round(m_disc["NVDA"][2], 2), "nvda_weight_as_of_pct": round(nvda_w, 2),
    "it_sector_weight_discussion_pct": sector_weight(m_disc, "Information Technology"),
    "it_sector_weight_as_of_pct": sector_weight(m_now, "Information Technology"),
    "nvda_price_discussion": PRICES["NVDA"][DISCUSSION], "nvda_price_as_of": nvda_price,
    "nvda_quantity": nvda_q, "nvda_market_value_as_of_usd": round(nvda_v, 2),
    "nvda_basis_per_share": BASIS[("C0007", "NVDA")],
    "nvda_unrealized_gain_as_of_usd": round(nvda_v - nvda_q * BASIS[("C0007", "NVDA")], 2),
    "total_value_discussion_usd": round(sum(v for q, v, w in m_disc.values()), 2),
    "total_value_as_of_usd": round(total_now, 2),
    "cash_and_equivalents_as_of_usd": round(m_now["CASH"][1] + m_now["SGOV"][1], 2),
    "trim_to_25pct_limit_usd": round(trim_usd, 2),
    "trim_realized_gain_usd": round(trim_usd * (1 - BASIS[("C0007", "NVDA")] / nvda_price), 2),
    "single_stock_limit_pct": 25.0, "sector_limit_pct": 40.0, "tax_budget_ips_v1_usd": 20000.0,
    "liquidity_reserve_usd": 50000.0,
}
status["finished"] = dt.datetime.utcnow().isoformat() + "Z"
notebookutils.fs.put(f"{ONELAKE}/Files/setup/portfolio_data_status.json", json.dumps(status, indent=2, default=str), True)
print(json.dumps(status["michael_scott"], indent=2))
notebookutils.notebook.exit(json.dumps({"tables": status["tables"], "michael_scott": status["michael_scott"]}, default=str))
