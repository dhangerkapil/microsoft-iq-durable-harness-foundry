---
name: portfolio-data
description: Answer portfolio questions from Fabric IQ, the portfolio system of record - weights, values and cash now against the investment policy limits, and how a position moved over a period - by asking the Fabric data agent for every figure by name. Not for a chart in the client's files, even one of a position against its limit - that is a client context question (document-finder).
---

Use this skill when the advisor asks for portfolio figures (weights, market values, sector exposure, cash, limits, or
how a position moved over time), for example where a position, the client's sector exposure and cash stand against
the client's limits. Answer from Fabric IQ alone.

1. In ONE step, call `query_portfolio_data` twice, in parallel, each a short question that names its figures: Fabric's
   data agent answers best when each figure is named, and one long question can run past the 100-second limit on a
   tool call. Ask for weights by their stored columns, so the data agent never computes one:
   - the latest figures: "<client>: on the latest date, total portfolio value (total_value_usd, which already includes
     cash), cash and equivalents, the Information Technology sector weight (sector_weight_pct), and <the position>'s
     weight (weight_pct), market value, shares, price and cost basis per share; and the single-stock limit, the sector
     limit and the liquidity reserve in the client's investment policy profile."
   - the movement, when asked: "<client>: <the position>'s weight_pct in positions_daily on each date over the last
     month, with its high and low."
   Don't write "signed" in a question: the data agent reads it as a plus or minus sign.
2. Answer under the heading "Portfolio data (Fabric IQ, as of <date>)" with a short table: figure, value, limit,
   status (within, or over by how much). Then the movement over the period: start, high, low and latest, each with
   its date.
3. If Fabric IQ leaves a figure out, say so; don't fill it from another source. If it returns
   FABRIC_CONSENT_REQUIRED, show the advisor its complete consent link.
