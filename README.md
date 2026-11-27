# AutoApply

A fully autonomous job applier that runs on your own computer. It finds new postings every hour, scores them
against your profile, fills in the application in a real browser window, submits it, reads the verification
codes and replies from your Gmail, and shows you exactly what went in every box.

## Start

```powershell
.\start.ps1
```

The first run creates `.env` and opens it: paste your `ANTHROPIC_API_KEY`, save, and run it again.
It then installs everything, builds the UI and opens http://127.0.0.1:8742.

Then, in the app:

1. **Profile**: upload your resume PDF. Claude reads it and fills your experience, education, projects and
   skills. Check the Contact, Eligibility and Self-identification sections; anything left blank is asked
   the first time a form needs it, and remembered after that.
2. **Settings → Accounts**: connect Gmail with an app password (Google Account → Security → 2-Step
   Verification → App passwords). This is what lets it handle emailed security codes, Workday's
   account-confirmation emails, and employer replies.
3. **Settings → Accounts → Sign in to Handshake** if you want Handshake: sign in once in the AutoApply
   browser window (it keeps its own profile, so it stays signed in), then turn Handshake on under
   *Where to look*.
4. **Home**: turn on Auto-apply, or press **Apply to new jobs now**.

## Chrome extension

The same brain, in your own browser: it fills an application in your real Chrome (your Handshake, Workday and
school sign-ins), you look it over and press Submit, and it logs the application in the app.

1. Open `edge://extensions` (Edge) or `chrome://extensions` (Chrome), turn on **Developer mode**, click
   **Load unpacked**, pick the `extension` folder.
2. Keep the app running (`.\start.ps1`).
3. On Greenhouse, Lever, Ashby, Workable, Workday or Handshake pages a **Fill with AutoApply** card appears.
   Anywhere else, click the extension icon → **Fill this application**. **Apply to this job later** hands the
   posting to autopilot instead.

Questions it can't answer from your profile are outlined in orange and listed in the card.

Want to check its work before anything is sent? Settings → *When a form is filled in* → **Wait for my OK
first**. Each filled form then shows up as *Ready for your OK* with a screenshot and every answer.

## What it does each hour

| Step | How |
|---|---|
| Find jobs | Public boards of ~500 companies on Greenhouse, Lever, Ashby and Workable, Workday career sites (searched by your roles), the SimplifyJobs new-grad and internship feeds, and Handshake in your signed-in browser. New company boards found in the feeds are added automatically. |
| Filter | Title vs. your roles (expanded: "Backend Engineer" also matches "Software Engineer"), level, US location / remote, posting age, companies to avoid. |
| Score | Claude rates fit 0–100 against your profile and checks your standing instructions. Below your minimum match is skipped. |
| Prioritize | Jobs go out in your order (Settings → *What to apply to first*): roles (e.g. AI engineer, then Software Engineer, then Data Engineer), company types (big tech, Fortune 500, defense & aerospace, government, startups, finance), then sites (*Where to look*). You choose which order wins, and whether companies of other types are applied to later or skipped. Claude sorts each employer by type once (about $0.04 per 1,000 companies). |
| Pace | Daily cap, at most 3 applications per company per 30 days, at least 2 days apart (all adjustable). |
| Apply | Opens the form in Chrome, reads every field, answers standard ones from your profile, reuses remembered answers, and has Claude write the rest from your profile and the posting. Uploads your resume (and a tailored cover letter PDF when there's a slot for one), submits, and confirms. |
| Verify | Emailed security codes (Greenhouse) and Workday confirmation links are read from Gmail. |
| Hand off | CAPTCHAs, unknown required questions, or a form it can't finish: the application goes to *Needs you* with the tab left open; finish it there and it notices. Answer a question once in the app and it's reused everywhere. |
| Replies | Gmail is checked every 10 minutes; employer emails are sorted (interview, assessment, rejection, ...) and attached to the application. |

### Workday

Each company's Workday needs its own candidate account. AutoApply creates it with your profile email and one
password (shown in Settings, so you can sign in yourself), confirms the account from Gmail, then picks
*Autofill with Resume* and walks the wizard: My Information, My Experience, Application Questions, Voluntary
Disclosures, Self Identify, Review, Submit.

### Honesty rules

Answers only use what's in your profile, your remembered answers and the job posting. Claude is told never to
invent experience, skills or numbers, and to leave factual questions it can't answer blank so you're asked.

## Layout

```
backend/app/
  main.py            HTTP API, serves the UI
  autopilot.py       background loop: discover → filter → score → queue → apply, inbox sync
  sources/           Greenhouse, Lever, Ashby, Workable, Workday, Simplify feeds
  matching.py        filters, Claude scoring, pacing rules
  apply/engine.py    one application end to end (open, fill page by page, submit, confirm)
  apply/dom.py       in-page field reader (labels, options, required, widgets)
  apply/answers.py   profile rules, option matching, Claude answers, cover letters
  apply/fill.py      fillers for text, selects, radios, comboboxes, Workday widgets, files, dates
  apply/workday.py   Workday accounts and wizard
  apply/handshake.py Handshake search and apply (in-app or external link)
  inbox.py           Gmail IMAP: codes, links, reply classification
frontend/            React UI (Vite)
data/                your database, resumes, screenshots, browser profile (local only)
```

Development: `.venv\Scripts\python backend\run.py` and, for live UI edits, `cd frontend; npm run dev`
(http://127.0.0.1:5173, proxies to the API).

## License

MIT. See [LICENSE](LICENSE).
