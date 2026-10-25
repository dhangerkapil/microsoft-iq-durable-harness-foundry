---
name: public-web-research
description: Bring in public news, filings and market context from Web IQ within the boundary rules - public topics only, never client data, and never repricing client positions.
---

Use this skill when the advisor asks for public news, a public filing or press release, analyst or market commentary,
or a public price or quote.

1. Build a public query: a company, ticker, sector or market event, and the theme (for example "NVIDIA AI data
   center outlook"). Never include a client's name, any number or amount (even a public one such as "250 million":
   the guard cannot tell public figures from client figures, so it blocks them all), account details, email
   addresses or anything from Microsoft 365: Web IQ is outside the Microsoft 365 and Azure boundary. If the guard
   returns `WEB_QUERY_BLOCKED`, say what was blocked and why in one line, rephrase once as a public topic, and
   continue.
2. Call `search_public_web` with kind `news` for recent articles, `web` for pages such as investor-relations or
   filings, or `finance` for a public quote. To read one article or page in full, call `read_public_web_page` with a
   link from the results.
3. Answer under a heading "Public news (Web IQ)", with each item's publisher or site, date and link. If Web IQ
   returns `WEB_UNAVAILABLE`, write "Public news (Web IQ): not checked; Web IQ is unavailable right now" in one line
   and continue with the other sources. Keep public facts separate from client facts: the
   portfolio system is the system of record for positions and valuations, so a public price never changes a
   client's figures. If a public price differs from the portfolio system's, say the client's figures come from the
   portfolio system and give the public price only as market context, with its time.
4. Summarize what the news means for the client's risk in neutral terms (what changed, what to ask), never as a
   recommendation to trade.
5. End the answer with the line "Public news doesn't reprice any client position: positions and valuations come only
   from the portfolio system.", also when the advisor asks for a set number of bullets.
