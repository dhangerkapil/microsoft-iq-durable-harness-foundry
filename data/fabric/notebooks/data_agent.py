# %% [markdown]
# # Advisor Harness: Portfolio Analyst data agent
# Creates, configures and publishes the Fabric data agent over the advisor lakehouse.
# Deploy-FabricPortfolio.ps1 replaces the __PLACEHOLDERS__ and runs this notebook.

# %%
%pip install -q fabric-data-agent-sdk

# %%
import datetime as dt
import json
import time
import traceback

WORKSPACE_ID = "__WORKSPACE_ID__"
LAKEHOUSE_ID = "__LAKEHOUSE_ID__"
LAKEHOUSE_NAME = "__LAKEHOUSE_NAME__"
DATA_AGENT_NAME = "__DATA_AGENT_NAME__"
ONELAKE = f"abfss://{WORKSPACE_ID}@onelake.dfs.fabric.microsoft.com/{LAKEHOUSE_ID}"
TABLES = ["clients", "ips_profiles", "securities", "prices_daily", "positions_daily", "position_changes",
          "client_summary_daily", "transactions"]
status = {"notebook": "advisor_data_agent", "started": dt.datetime.utcnow().isoformat() + "Z", "steps": []}


def save_status():
    notebookutils.fs.put(f"{ONELAKE}/Files/setup/data_agent_status.json", json.dumps(status, indent=2, default=str), True)


AGENT_DESCRIPTION = (
    "Portfolio Analyst for Contoso Wealth advisors. Answers questions about client holdings, weights, "
    "concentration and sector limits, unrealized gains, cash and liquidity, investment policy (IPS) limits, "
    "transactions and position changes between dates. Synthetic demo data."
)
AGENT_INSTRUCTIONS = """You are the Portfolio Analyst for Contoso Wealth advisors.
Answer only from the lakehouse tables. All data is synthetic demo data, not market data.
- The latest snapshot is MAX(as_of_date) in positions_daily. State the as_of_date for every figure you report.
- Weights are percentages of total portfolio value including cash.
- Single-stock limits apply to individual equities (asset_class = 'Equity'). Sector limits apply to the
  combined weight of individual equities in a sector.
- Use position_changes for changes between two dates. Use client_summary_daily for totals, cash and
  equivalents and breach counts. Use ips_profiles for limits, tax budget and liquidity reserve.
- Report US dollars with two decimals and percentages with one decimal.
- Never recommend trades or give investment, tax or legal advice. Report facts and flag limit breaches."""
DATASOURCE_INSTRUCTIONS = """Tables in schema dbo. Join on client_id; match client names exactly on clients.client_name.
- clients: one row per client (client_id, client_name, segment, risk_profile, city, state, client_since).
- ips_profiles: investment policy limits per client (single_stock_limit_pct, sector_limit_pct,
  annual_tax_budget_usd, liquidity_reserve_usd, annual_income_need_usd, esg_preference, ips_version,
  ips_effective_date, next_review_date, ips_document).
- securities: ticker reference data (security_name, sector, asset_class, esg_rating).
- prices_daily: daily close_price per ticker and as_of_date.
- positions_daily: one row per client, ticker and trading day with quantity, close_price, market_value_usd,
  weight_pct, cost_basis_usd, unrealized_gain_usd, single_stock_limit_pct, over_single_stock_limit,
  sector_weight_pct, sector_limit_pct and over_sector_limit. Boolean columns are bit (1 = true).
- position_changes: precomputed per client and ticker between from_date and to_date. Every pair ends on
  2026-10-19 and starts on 2026-06-30, 2026-09-30 or 2026-10-16.
- client_summary_daily: daily totals per client (total_value_usd, cash_and_equivalents_usd,
  unrealized_gain_usd, largest_equity_ticker, largest_equity_weight_pct, positions_over_single_stock_limit,
  sectors_over_limit).
- transactions: trades, dividends, deposits and withdrawals with cash_amount_usd.
Use MAX(as_of_date) for the latest snapshot unless the question names a date."""
LATEST = "(SELECT MAX(as_of_date) FROM dbo.positions_daily)"
FEW_SHOTS = {
    "What is Michael Scott's largest position today and is it above his single-stock limit?":
        "SELECT TOP 1 p.as_of_date, p.ticker, p.weight_pct, p.single_stock_limit_pct, p.over_single_stock_limit, "
        "p.market_value_usd FROM dbo.positions_daily p JOIN dbo.clients c ON c.client_id = p.client_id "
        f"WHERE c.client_name = 'Michael Scott' AND p.asset_class = 'Equity' AND p.as_of_date = {LATEST} "
        "ORDER BY p.weight_pct DESC",
    "How did Michael Scott's positions change since 2026-10-16?":
        "SELECT ch.ticker, ch.from_date, ch.to_date, ch.weight_from_pct, ch.weight_to_pct, ch.weight_change_pts, "
        "ch.value_change_usd, ch.price_change_pct FROM dbo.position_changes ch "
        "JOIN dbo.clients c ON c.client_id = ch.client_id WHERE c.client_name = 'Michael Scott' "
        "AND ch.from_date = '2026-10-16' AND ch.to_date = '2026-10-19' ORDER BY ABS(ch.weight_change_pts) DESC",
    "Which clients have positions above their single-stock limit today?":
        "SELECT c.client_name, p.ticker, p.weight_pct, p.single_stock_limit_pct FROM dbo.positions_daily p "
        "JOIN dbo.clients c ON c.client_id = p.client_id WHERE p.over_single_stock_limit = 1 "
        f"AND p.as_of_date = {LATEST} ORDER BY p.weight_pct DESC",
    "What are Michael Scott's cash and equivalents compared with his liquidity reserve and tax budget?":
        "SELECT s.as_of_date, s.total_value_usd, s.cash_and_equivalents_usd, i.liquidity_reserve_usd, "
        "i.annual_tax_budget_usd, i.ips_version FROM dbo.client_summary_daily s "
        "JOIN dbo.ips_profiles i ON i.client_id = s.client_id WHERE s.client_name = 'Michael Scott' "
        f"AND s.as_of_date = {LATEST}",
    "What is the unrealized gain and cost basis on Michael Scott's NVDA position?":
        "SELECT p.as_of_date, p.quantity, p.close_price, p.market_value_usd, p.cost_basis_usd, p.unrealized_gain_usd "
        "FROM dbo.positions_daily p JOIN dbo.clients c ON c.client_id = p.client_id "
        f"WHERE c.client_name = 'Michael Scott' AND p.ticker = 'NVDA' AND p.as_of_date = {LATEST}",
    "What is Michael Scott's sector exposure today versus his sector limit?":
        "SELECT p.sector, MAX(p.sector_weight_pct) AS sector_weight_pct, MAX(p.sector_limit_pct) AS sector_limit_pct, "
        "MAX(CAST(p.over_sector_limit AS int)) AS over_sector_limit FROM dbo.positions_daily p "
        "JOIN dbo.clients c ON c.client_id = p.client_id WHERE c.client_name = 'Michael Scott' "
        f"AND p.asset_class = 'Equity' AND p.as_of_date = {LATEST} GROUP BY p.sector ORDER BY sector_weight_pct DESC",
}


def step(name, fn):
    try:
        result = fn()
        status["steps"].append({"step": name, "ok": True})
        return result
    except Exception:
        status["steps"].append({"step": name, "ok": False, "error": traceback.format_exc()[-2500:]})
        save_status()
        raise

# %%
from fabric.dataagent.client import FabricDataAgentManagement, create_data_agent, delete_data_agent

try:
    delete_data_agent(DATA_AGENT_NAME)
    status["steps"].append({"step": "delete existing", "ok": True})
except Exception as exc:
    status["steps"].append({"step": "delete existing", "ok": False, "note": str(exc)[:300]})

agent = step("create", lambda: create_data_agent(DATA_AGENT_NAME))


def configure_agent():
    try:
        agent.update_configuration(instructions=AGENT_INSTRUCTIONS, description=AGENT_DESCRIPTION)
    except TypeError:
        agent.update_configuration(instructions=AGENT_INSTRUCTIONS)


step("instructions", configure_agent)
step("add lakehouse", lambda: agent.add_datasource(LAKEHOUSE_NAME, type="lakehouse"))
datasource = step("datasource", lambda: agent.get_datasources()[0])


def select_tables():
    selected, errors = [], {}
    for table in TABLES:
        for attempt in range(15):
            try:
                datasource.select("dbo", table)
                selected.append(table)
                break
            except Exception as exc:
                errors[table] = str(exc)[:300]
                time.sleep(20)
    status["selected_tables"], status["select_errors"] = selected, {t: e for t, e in errors.items() if t not in selected}
    if len(selected) != len(TABLES):
        raise RuntimeError(f"Tables not available to the data agent: {status['select_errors']}")


step("select tables", select_tables)
step("datasource instructions", lambda: datasource.update_configuration(instructions=DATASOURCE_INSTRUCTIONS))
step("few-shot examples", lambda: datasource.add_fewshots(FEW_SHOTS))


def publish():
    # publish_staging is the current SDK call; publish() on recent SDKs returns without publishing.
    if hasattr(agent, "publish_staging"):
        agent.publish_staging(description="Portfolio Analyst for the advisor harness demo (synthetic data).")
    else:
        agent.publish()


step("publish", publish)
status["finished"] = dt.datetime.utcnow().isoformat() + "Z"
save_status()
print(json.dumps(status, indent=2))
notebookutils.notebook.exit(json.dumps({"published": True, "selected_tables": status["selected_tables"]}))
