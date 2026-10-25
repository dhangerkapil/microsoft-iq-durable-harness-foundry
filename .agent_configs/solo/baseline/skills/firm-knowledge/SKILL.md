---
name: firm-knowledge
description: Answer what the firm requires, offers or thinks (policies, procedures, approved products, planning guides, the investment committee's house view) from Foundry IQ, and apply it to the client's situation with citations.
---

Use this skill when the advisor asks what the firm's policy or procedure says, what needs to happen before an action
(a trim, a large withdrawal or wire, a new account), which products are approved, or what the house view is.

1. In ONE step, call `search_firm_knowledge` once per distinct question, in the advisor's words (one complete
   question each, under 400 characters). For a named client, also call `recall_client_memory`, and the Work IQ or
   Fabric IQ calls the situation needs (for example the client's latest email or position), in the same step.
2. Answer with the rule and its source: quote or closely paraphrase the passage, then cite it as document code and
   section followed by "(Foundry IQ)", for example "OPS-220 §2 (Foundry IQ)", so the advisor sees which source
   answered. When the answer has several parts, put the rules under a heading "Firm policy (Foundry IQ)". A firm
   rule says what must happen; it is not a fact about the client.
3. When a client is involved, apply each rule to the client's facts from the other sources and say what it means
   in practice: for example, a position above its limit triggers a review with a deadline; a wire above a threshold
   needs a signed authorization, a call-back and a second approver, started a set number of business days before
   the date the money is needed. Name the client fact's source and date next to the rule's citation.
4. If no passage answers the question, say the firm library does not cover it; do not answer from general
   knowledge. Never present a house view as a recommendation for the client.
