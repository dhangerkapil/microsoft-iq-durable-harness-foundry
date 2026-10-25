// Story dates: moves the demo story's week to any Thursday client meeting.
// Same rules as hydration/story_dates.py (the reference) and scripts/StoryDates.ps1: STORY_MEETING_DATE
// (yyyy-mm-dd, a Thursday) moves every story date by whole weeks from the original Thursday 2026-10-22
// meeting. Unset or 2026-10-22 changes nothing. scripts/Test-StoryDates.ps1 checks that the three agree.
"use strict";

const DAY = 86400000;
const ORIGINAL = Date.UTC(2026, 9, 22);
const FULL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const SEASON_MONTH = { winter: 1, spring: 4, summer: 7, fall: 10, autumn: 10 };
const YEAR = "202[5-7]";
const MONTHS = [...FULL, "Sept", ...ABBR.filter((a) => a !== "May")].join("|");
const PATTERN = new RegExp(
  `(?<![\\d-])(?<iy>${YEAR})-(?<im>\\d\\d)-(?<id>\\d\\d)(?!\\d)` +
  `|\\b(?<mn>${MONTHS})(?<dot>\\.?) (?<d>\\d{1,2})(?:, (?<y>${YEAR}))?\\b` +
  `|\\b(?<mmn>${MONTHS}) (?<my>${YEAR})\\b` +
  `|\\b(?<pre>since|from|until|through|before|after|in|by) (?<bm>${FULL.join("|")})\\b(?![ .]\\d)` +
  `|\\b(?<sn>[Ss]pring|[Ss]ummer|[Ff]all|[Aa]utumn|[Ww]inter) (?<sy>${YEAR})\\b` +
  `|\\b(?<bp>than|before|until|by) (?<bs>spring|summer|fall|autumn|winter)\\b(?! \\d)` +
  `|\\bQ(?<q>[1-4])\\b` +
  `|(?<![\\d-])\\b(?<year>${YEAR})\\b(?![-\\d])`,
  "g",
);

// A UTC timestamp for a calendar date, or null when the date does not exist.
function ymd(y, m, d) {
  const t = Date.UTC(y, m - 1, d);
  const x = new Date(t);
  return x.getUTCFullYear() === y && x.getUTCMonth() === m - 1 && x.getUTCDate() === d ? t : null;
}
const parts = (t) => { const x = new Date(t); return { y: x.getUTCFullYear(), m: x.getUTCMonth() + 1, d: x.getUTCDate() }; };
const iso = (t) => new Date(t).toISOString().slice(0, 10);

function meetingDate(value) {
  const text = String(value || process.env.STORY_MEETING_DATE || "").trim() || iso(ORIGINAL);
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  const t = match ? ymd(Number(match[1]), Number(match[2]), Number(match[3])) : null;
  if (t === null) throw new Error(`STORY_MEETING_DATE must be yyyy-mm-dd; got '${text}'.`);
  if (new Date(t).getUTCDay() !== 4) throw new Error(`STORY_MEETING_DATE must be a Thursday; ${text} is not.`);
  return t;
}

const shiftDays = (value) => Math.round((meetingDate(value) - ORIGINAL) / DAY);
const monthIndex = (name) => { const n = name === "Sept" ? "Sep" : name; const i = FULL.indexOf(n); return (i >= 0 ? i : ABBR.indexOf(n)) + 1; };
const monthName = (index, like) => (FULL.includes(like) ? FULL[index - 1] : like === "Sept" && index === 9 ? "Sept" : ABBR[index - 1]);

function nearest(month, day) {
  let best = null;
  for (const year of [2025, 2026, 2027]) {
    const t = ymd(year, month, day);
    if (t === null) continue;
    if (best === null || Math.abs(t - ORIGINAL) < Math.abs(best - ORIGINAL)) best = t;
  }
  return best;
}

function seasonName(t, like) {
  let name = ["winter", "winter", "spring", "spring", "spring", "summer", "summer", "summer", "fall", "fall", "fall", "winter"][parts(t).m - 1];
  if (name === "fall" && like.toLowerCase() === "autumn") name = "autumn";
  return like[0] === like[0].toUpperCase() ? name[0].toUpperCase() + name.slice(1) : name;
}

function shiftText(text, days) {
  const shift = days === undefined || days === null ? shiftDays() : days;
  if (!shift || !text) return text;
  const yearDelta = parts(ORIGINAL + shift * DAY).y - parts(ORIGINAL).y;
  return String(text).replace(PATTERN, (match, ...args) => {
    const g = args[args.length - 1];
    const move = (t) => (t === null ? null : t + shift * DAY);
    if (g.iy) {
      const t = move(ymd(Number(g.iy), Number(g.im), Number(g.id)));
      return t === null ? match : iso(t);
    }
    if (g.mn) {
      const month = monthIndex(g.mn);
      const t = move(g.y ? ymd(Number(g.y), month, Number(g.d)) : nearest(month, Number(g.d)));
      if (t === null) return match;
      const p = parts(t);
      const out = `${monthName(p.m, g.mn)}${g.dot} ${p.d}`;
      return g.y ? `${out}, ${p.y}` : out;
    }
    if (g.mmn) {
      const p = parts(move(ymd(Number(g.my), monthIndex(g.mmn), 15)));
      return `${monthName(p.m, g.mmn)} ${p.y}`;
    }
    if (g.bm) {
      const t = move(nearest(monthIndex(g.bm), 1));
      return t === null ? match : `${g.pre} ${FULL[parts(t).m - 1]}`;
    }
    if (g.sn) {
      const t = move(ymd(Number(g.sy), SEASON_MONTH[g.sn.toLowerCase()], 15));
      return `${seasonName(t, g.sn)} ${parts(t).y}`;
    }
    if (g.bs) {
      let next = ymd(parts(ORIGINAL).y, SEASON_MONTH[g.bs], 15);
      if (next <= ORIGINAL) next = ymd(parts(ORIGINAL).y + 1, SEASON_MONTH[g.bs], 15);
      return `${g.bp} ${seasonName(move(next), g.bs)}`;
    }
    if (g.q) return `Q${Math.floor((parts(move(ymd(parts(ORIGINAL).y, 3 * Number(g.q) - 2, 16))).m - 1) / 3) + 1}`;
    return String(Number(g.year) + yearDelta);
  });
}

module.exports = { ORIGINAL_MEETING: iso(ORIGINAL), meetingDate: (v) => iso(meetingDate(v)), shiftDays, shiftText };

if (require.main === module) {
  // Usage: node story-dates.cjs <days|-> < text   (prints the shifted text; "-" reads STORY_MEETING_DATE)
  const arg = process.argv[2] || "-";
  const input = require("fs").readFileSync(0, "utf8");
  process.stdout.write(shiftText(input, arg === "-" ? undefined : Number(arg)));
}
