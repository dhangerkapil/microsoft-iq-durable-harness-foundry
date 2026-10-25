---
name: portfolio-data
description: Answer portfolio questions from Fabric IQ, the portfolio system of record - weights, values and cash now against the investment policy limits, and how a position moved over a period - by asking the Fabric data agent for every figure by name.
---

Use this skill when the advisor asks for portfolio figures (weights, market values, sector exposure, cash, limits, or
how a position moved over time), for example where a position, the client's sector exposure and cash stand against
the client's limits. Answer from Fabric IQ alone.

1. Call `query_portfolio_data` once, with one question that names every figure the answer needs; the data agent
   answers best when each figure is named. For example: "<client>: on the latest date, total portfolio value, cash
   and equivalents, the Information Technology sector weight (sum of equity positions in Information Technology as a
   percent of total portfolio value), and <the position>'s weight, market value, shares, price and cost basis per
   share; the single-stock limit, the sector limit and the liquidity reserve in the client's investment policy
   profile; and <the position>'s weight on each date over the last month, with its high and low." Don't write
   "signed" in the question: the data agent reads it as a plus or minus sign.
2. Answer under the heading "Portfolio data (Fabric IQ, as of <date>)" with a short table: figure, value, limit,
   status (within, or over by how much). Then the movement over the period: start, high, low and latest, each with
   its date.
3. If Fabric IQ leaves a figure out, say so; don't fill it from another source. If it returns
   FABRIC_CONSENT_REQUIRED, show the advisor its complete consent link.
