# Recorded meeting script: Q4 portfolio review with Michael Scott

All names, numbers and conversations are fictional and for demo purposes only.

Record this on **Friday, October 16**, in the Teams meeting **"Q4 portfolio review - Michael Scott"**, so Work IQ can use the recording's transcript as the "last discussion". It takes about four minutes. The dates here are the reference story's; the Content step (`data\hydration\Publish-DemoContent.ps1 -Stage Meeting`) prints your story week and writes a copy of this script with your dates to the work folder (`meeting-script.md` in `%LOCALAPPDATA%\advisor-team\<profile>\work`).

**Who organizes it:** the analyst (`-AnalystUpn` of the deploy script, `DEMO_ANALYST_UPN` in `.env.local`). A tenant admin's mailbox often can't host Teams meetings (its calendar's `allowedOnlineMeetingProviders` is empty, so Outlook shows no Teams toggle), so the Content step creates the meeting in the analyst's calendar with the advisor (the admin who ran the deployment) invited, for **Friday 10:30-11:00 PM ET**. The join link works at any time. To check an organizer: `GET /users/{upn}/calendar?$select=allowedOnlineMeetingProviders` must include `teamsForBusiness`.

**When:** record it on the Friday evening if you can, so the transcript is dated Friday like the rest of the story. Ideally finish before **9:00 AM ET on Saturday**: Michael's "closing moved" email is dated Saturday 10:24 AM and comes after the meeting. A later recording still works; the agent then sees a transcript dated after that email.

## Who signs in where

| Window | Account | Role in the meeting |
|---|---|---|
| 1: Edge **InPrivate** | The advisor: the admin account that ran the deployment | The advisor. **You speak only from this window**, both parts. |
| 2: a **different browser** (Chrome, or a separate Edge profile; not a second InPrivate window, which shares the first one's sign-in) | The analyst | Organizer. Starts and stops the recording; no audio. If you don't know the analyst's password, reset it in the Microsoft 365 admin center: Users > Active users > the analyst > Reset password. |

## Steps

1. **Window 2 (analyst):** open <https://teams.microsoft.com>, go to **Calendar**, open **Q4 portfolio review - Michael Scott** (Friday 10:30 PM), select **Join**. On the join screen choose **Don't use audio** (or turn the microphone and speaker off), then **Join now**.
2. **Window 1 (advisor):** open <https://teams.microsoft.com>. If it offers the desktop app, choose **Use the web app instead**. Go to **Calendar**, open the same meeting (you're an invitee), select **Join** with **Computer audio** and the microphone on. If you wait in the lobby, admit yourself from window 2.
3. **Window 2 (analyst):** **More (...)** > **Record and transcribe** > **Start recording**. Check that the banner says recording **and transcription** have started; if only recording started, also choose **Record and transcribe** > **Start transcription**.
4. **Window 1 (advisor):** read the script below aloud, both parts. Say **"Michael says"** before each of Michael's lines, so the transcript shows who said what.
5. **Window 2 (analyst):** **Record and transcribe** > **Stop recording**, then **Leave** > **End meeting**. Close window 2.
6. Wait about 10 minutes for the transcript, then ask the agent as the advisor: *In Friday's Q4 portfolio review, what did Michael say about his house?* It should quote the transcript. Then run the Monday stage and save the baseline (the deploy script's closing notes list both commands).

## Script

**Advisor:** Thanks for making time, Michael. This is our Q4 review. I want to cover four things: your NVDA position, the house, your ESG question, and the tax budget.

**Michael:** Sounds good. NVDA first, it's been on a tear.

**Advisor:** It has. As of today NVDA is 24.1 percent of your portfolio, and your investment policy has a 25 percent single-stock limit. Technology overall is about 39 percent against a 40 percent sector limit. So we're close on both.

**Michael:** What happens if it goes over?

**Advisor:** If NVDA goes above 25 percent, I'll propose a trim back to the limit. Your cost basis is very low, around 41 dollars a share, so any sale realizes a large gain. We'd stage it so the realized gains stay inside your annual tax budget, which is 20 thousand dollars in your current policy.

**Michael:** About that. My accountant thinks we should lower the budget to 15 thousand for this year. I'll confirm with her.

**Advisor:** Thanks. Let me know and I'll update the policy. Next, the house.

**Michael:** We're planning to buy in spring 2027. We'll need about 400 thousand dollars for the down payment.

**Advisor:** Understood. We'll keep your 50 thousand dollar liquidity reserve untouched and plan where the 400 thousand comes from well before then.

**Michael:** And my niece keeps telling me about ESG investing. Can new money go that way?

**Advisor:** Yes. I'll add an ESG preference for new contributions to your investment policy. I'll have a version 2 draft ready for you by Monday with the ESG preference and the tax budget question.

**Advisor:** To recap the action items. I'll draft IPS version 2 by Monday, watch NVDA against the 25 percent limit, and outline how to fund the down payment. You'll confirm the house timing and the tax budget with your accountant. Our next meeting is year-end planning on Thursday, October 22.

**Michael:** Perfect. Talk Thursday.
