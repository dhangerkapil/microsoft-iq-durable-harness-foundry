---
name: rebalancing-guard
description: Check a concentrated position or a client cash need against the investment policy limits, the realized-gain tax budget and the liquidity reserve with exact arithmetic, and present facts and trade-offs without giving advice.
---

Use this skill when a position or sector is above its policy limit, a trim is being considered, or the client
needs cash from the portfolio. When the advisor asks how to fund a client's cash need and what must happen first,
load microsoft-iq-answer instead: it applies this skill's checks.

1. Collect the inputs with Work IQ, each with its source and date: position market value, total portfolio value,
   latest price, cost basis per share and cash and equivalents (the newest portfolio snapshot: the analyst's email
   or the portfolio overview PDF); the limits, the tax budget and the liquidity reserve (the signed IPS); and any
   draft IPS or client email that changes the tax budget, the timing of cash needs or preferences. Use
   `query_portfolio_data` only if a figure is missing or the advisor asks for live portfolio data.
2. Call `rebalancing_check` once for each tax budget in play (for example the signed IPS budget and a lower budget
   from a draft or a client email), with `proceeds_withdrawn=false`: a trim back to the limit keeps the proceeds in
   the portfolio as cash. Use `proceeds_withdrawn=true` only when the advisor asks how much to sell to fund a
   withdrawal; a cash need such as a down payment is a separate `liquidity_check`. If no source gives the gains
   realized so far this year, pass 0 and say the result assumes none.
3. Call `liquidity_check` for cash needs, with each need labelled with its amount and deadline.
4. Report the facts: the breach size, the trim needed to reach the limit, shares to sell, realized gain, whether
   it fits each tax budget, the largest trim that fits, and the liquidity shortfall. Name the as-of date of every
   figure.
5. Present trade-offs as questions for the advisor and client (for example staging a trim across tax years, or
   funding the cash need from other holdings). Do not recommend trades, and say that tax treatment must be
   confirmed with the client's tax adviser.
