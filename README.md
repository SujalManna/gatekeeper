# gatekeeper

Vendor verification for the ContextCon Trust & compliance track: is a new vendor **real, trading, and
run by the people it says**, and are its directors clean in the news, with namesakes cleared.
Every fact in the answer points at its source.

Architecture and data flow: [docs/architecture.md](docs/architecture.md).

## The job

Before a business starts paying a new carrier or supplier, someone has to check that the vendor
exists, is active, is run by its stated director, and isn't a front, and that the directors aren't in
the news for fraud. Today, compliance teams at banks, payment firms and large buyers pay specialist
verification and screening firms **per business or per person checked**, or have analysts do it by
hand from registries, websites and news searches.

**Our client** is a mid-size Indian third-party logistics (3PL) company that clears new carriers
and suppliers every week. A wrong "cleared" means paying a front; a wrong "looks off" means
turning away an honest carrier. So the tool is built to say *I can't verify this* rather than guess.

## Input and output

```powershell
python run.py --name "Shadowfax Technologies" --website "https://www.shadowfax.in"
```

The answer is printed and saved to `output/<job_id>.json`:

| Field | What it holds |
|---|---|
| `verdict` | `REAL_AND_TRADING`, `LOOKS_OFF` or `CANNOT_VERIFY` |
| `verdict_basis`, `missing` | why, and what could not be verified |
| `summary` | who runs it, age, status, HQ, size, entity link, operating signals, anything off |
| `checks`, `red_flags` | each check's status and every flag (`concrete`, `warning`, `note`) |
| `entity_resolution` | how the website, company record and directors were linked, with `match_confidence` and the signals used |
| `adverse_media`, `company_adverse_media` | every news hit, its verdict and the reason |
| `evidence` | **every fact**: source (URL or API endpoint + request), exact field or passage, value, record date, `retrieved_at` |
| `sources_degraded`, `retries` | every source that was slow or down, and every retry |
| `approval` | the pending approval, and the command to approve it |
| `cost_credits`, `run_cost_credits`, `runtime_seconds`, `step_seconds` | what it cost and how long it took |

## Pass mark

1. **Never a wrong verdict.** When in doubt, `CANNOT_VERIFY` with a list of what's missing.
2. **Honest firms and namesakes pass.** A director who shares a name with someone in a fraud story
   isn't flagged on the name alone. Being named as a plaintiff, visa applicant or victim isn't being
   accused. A plain lawsuit, FIR or payment dispute is `possible_adverse`: it needs a person to
   read it, and it never makes LOOKS_OFF on its own.
3. **Fronts are caught only with evidence.** `LOOKS_OFF` needs a concrete negative backed by a
   source we actually read. Examples: an article accusing the company of fraud, money laundering or
   being a shell, or its directors arrested or charged; a lookalike of a real company's domain;
   a parked site. Active on paper with no operating signals is only a warning (`possible_front`).
4. **Unavailable sources mean `CANNOT_VERIFY`.** A timeout or outage is never read as "nothing
   found" and never produces `LOOKS_OFF`. A domain that doesn't exist (NXDOMAIN) is different: it's a
   definite "no record".

The test suite (`tests/cases.json`, 76 cases) encodes this:

| Case | Verdict |
|---|---|
| Retool (`retool.com`) | REAL_AND_TRADING |
| Shadowfax Technologies (`shadowfax.in`) | REAL_AND_TRADING |
| A real Nagpur shell company, reported Nov 2026 | LOOKS_OFF: accusing articles in the evidence |
| `shadowfaax.in`, `retooI.com` (lookalikes that don't exist) | LOOKS_OFF |
| Delhivery's name on retool.com (borrowed website) | CANNOT_VERIFY |
| A made-up vendor whose domain doesn't exist | CANNOT_VERIFY |
| Every Crustdata call timing out (mocked, offline) | CANNOT_VERIFY |

## Data sources

| Source | Call | Used for |
|---|---|---|
| Crustdata | `POST /company/identify` (by domain, by name) | Which company the website and the name point to; lookalike and borrowed-site detection |
| Crustdata | `POST /company/enrich` (`basic_info`, `locations`, `updated_at`) | Name, status, year founded (age), size, HQ, profile URL, record date |
| Crustdata | `POST /person/search` | Founders, CEO and directors currently at the company; the count of current employees (an operating signal) |
| Crustdata | `POST /person/enrich` (`basic_profile`, `experience`, `social_handles`) | Confirms each leader's current employer is this company; city for namesake clearing |
| Crustdata | `POST /job/search` | Job postings in the last 180 days (an operating signal) |
| Crustdata | `POST /web/search/live` | News about the company; news about directors when GDELT is down; readable copies of login-walled posts |
| Crustdata | `POST /web/enrich/live` | The vendor's website and its contact / about / privacy pages; reading news articles (`human_mode` for redirect links) |
| GDELT DOC 2.0 | `api.gdeltproject.org/api/v2/doc/doc` (free, no key) | News first for directors, and alongside Crustdata for the company; direct links, `seendate` as record date |
| DNS-over-HTTPS | `dns.google/resolve`, `cloudflare-dns.com/dns-query` | Does the website's domain exist at all (NXDOMAIN vs timeout) |

Field names come from Crustdata's API reference (API version `2025-11-01`); nothing is invented.
Two gaps are surfaced, not filled: Crustdata has no legal-name field, so `legal_name` is null with a
note, and person record dates aren't on this plan.

## How to run

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Put your Crustdata key in `.env` (never committed):

```text
CRUSTDATA_API_KEY=...
```

| Command | What it does |
|---|---|
| `python run.py --name "<vendor>" --website "<url>"` | Check a vendor. Running it again **resumes**: finished steps are skipped, API calls come from the cache. |
| `python run.py ... --recheck` | Re-run every step from cached responses (no re-spend); approvals are kept. |
| `python approve.py <job_id>` / `--reject` | The only way to mark a vendor cleared. Refuses unless a request is pending and the saved verdict is REAL_AND_TRADING. |
| `python run_tests.py` | Unit + mocked-outage tests: offline, no credits. |
| `python run_tests.py --e2e [--recheck] [-k text]` | Also the live end-to-end vendor cases, each with its runtime and credits. |

Limits live in `config.json`: `cost_limit_credits_per_run` (60) and `time_limit_seconds_per_run` (180).
`approve.py` also takes `--by "<name>"` to record who decided (default: your OS user name).

## Web app and deployment

The web app is a **FastAPI** backend (`server.py`) and a **React + TypeScript** frontend (`web/`, built
with Vite). The UI uses **Material Design 3** through Google's Material Web components (`@material/web`).
Its color scheme is generated from the verdigris seed `#0F7B5F` with Material's color utilities. On top
sits the app's own "Inspection Trail" look: a live, numbered trail of checks that ends in a verdict seal. Both deploy to **Vercel** as one project with two services (`vercel.json`): `/api/*` goes
to the API, and everything else goes to the frontend.

| API | What it does |
|---|---|
| `POST /api/checks` | Runs a check (`run.py` in its own process) and streams progress as NDJSON, then the result |
| `GET /api/checks`, `GET /api/checks/{job_id}` | Recent checks; one full result |
| `POST /api/checks/{job_id}/approval` | Approve or reject, with the approver's name. Calls the same `approve.decide()` as the CLI |
| `GET /api/config`, `GET /api/health` | Pricing and limits; liveness (the only unauthenticated endpoint) |

Interactive API docs are at `/api/docs`.

- **Access:** every endpoint except `/api/health` needs `Authorization: Bearer <APP_ACCESS_TOKEN>`. The
  frontend asks for the token once per browser tab.
- **Storage:** with `DATABASE_URL` set (production), the job store, steps, ledger, evidence,
  approvals, results and API cache live in Postgres. Vercel functions keep no files between requests.
  Without it, it's SQLite and files, as for the CLI.
- **Time:** a check runs inside one request (the function limit is 300 s; the run deadline is 165 s).
  If the browser disconnects, the run stops, and running it again resumes from the saved steps.

**Deploying (Vercel builds everything; nothing to install locally):**

1. Import the GitHub repo as a new Vercel project. Vercel detects the services from `vercel.json`.
2. Add a Postgres database from the Vercel Marketplace (Neon). It sets `DATABASE_URL`.
3. Set these environment variables in Vercel:
   - `CRUSTDATA_API_KEY`
   - `APP_ACCESS_TOKEN`: a long random string, which you then share with the people who use the app
4. Deploy. Every push to `main` redeploys; other branches get preview URLs.

**Running it locally (optional, needs Node.js and the Python packages):**

```powershell
pip install -r requirements.txt uvicorn
$env:APP_ACCESS_TOKEN = "dev-token"; uvicorn server:app --port 8000
```

```powershell
cd web; npm install; npm run dev
```

## Robustness

- **Restart-safe steps** (`store.py`, SQLite `gatekeeper.db`). The job ID is a hash of name +
  domain. Each step is saved only when it finishes, so a run killed at any point (tested with a
  real Ctrl+C) resumes where it stopped. A step that hit an unavailable source is re-run next time.
- **Response cache** (`cache/`). Every successful API response is stored with its retrieval time,
  written atomically, so repeat calls cost 0 and every fact can cite when it was retrieved. Pages
  that failed or hit a bot check are not cached, so they get retried.
- **Retries.** Retries happen on 429 / 5xx / timeouts with backoff, and every attempt is logged in the `retries` table and in the
  output. A run deadline 15 s before the time limit means no request or retry runs past it; the
  source is then "unavailable". GDELT gets one attempt, then is skipped for 15 minutes.
- **Speed.** Independent steps, the leaders' screenings and page fetches run in parallel (3 workers,
  within rate limits). `human_mode` fetches are capped at 8 per run. Cold runs on new vendors
  took 56–119 s against a 180 s limit; cached re-checks take 1–21 s.
- **Cost ledger** (`ledger` table). Every call is logged with what Crustdata actually charged, from
  the `X-Credits-Used` response header (the config estimate is used only if the header is missing).
- **Budget stop.** Before each paid call, the estimate is checked against the per-run budget. Going
  over stops the run with `CANNOT_VERIFY`, and the next run resumes.

## Ethics

- **Minimal personal data.** For directors, the output and evidence keep name, role, city and public
  profile URL. No contact data is requested: no emails, no phone numbers. The raw API cache holds the
  profile sections we request (basic profile, experience, social handles), stays on the machine, and
  is excluded from git along with the database and outputs (`.gitignore`).
- **No flag on a name alone.** A news hit counts only if it names the person *and* their company, role
  or city. A same-city namesake becomes `possible_adverse` (a person reads it), not `adverse`.
- **Mentioned ≠ accused.** A plaintiff, applicant, victim or complainant is not accused. A company
  that is impersonated or files a complaint is not accused. A lawsuit or payment dispute is not fraud.
- **A person approves.** `run.py` only *requests* approval, and only for REAL_AND_TRADING.
  `approve.py` is the only path to "cleared". A request is withdrawn if a later run changes the verdict.
- **Every verdict is explained.** It lists its basis, its checks, its flags, and every source and passage.
- **No personal names in the repo.** The test case for the real shell company asserts only the
  company-level finding; the people named in the article are not committed.

## Unit economics

**Measured cost.** From the ledger, over all test runs:

| Measure | Credits | USD (1 credit = $0.30) | INR at ₹92.66/$ |
|---|---|---|---|
| Average per vendor job, all 12 test vendors, **including repeated re-checks** | 25.86 | $7.76 | **₹719** |
| Average **first run** on 5 new vendors (Delhivery, Ecom Express, Rivigo, Xpressbees, BlackBuck) | 19.76 | $5.93 | **₹549** |

- **Credit price:** $0.30 per credit, from 15,000 credits for $4,500.
- **Exchange rate:** 1 USD = ₹92.66 (x-rates.com, 22 Nov 2026, 08:53 UTC).
- **Where the cost goes:** page fetches (`/web/enrich/live`) are 63% of all credits spent, followed by
  person search (12%) and web search (12%). GDELT and DNS are free.

**Proposed price: ₹999 per vendor cleared.** Only vendors that reach REAL_AND_TRADING and are
approved are billed. The cost of the others is spread over them, assuming 85% of submitted vendors
clear:

| | First-run cost (19.76 cr) | Measured average (25.86 cr) |
|---|---|---|
| Cost per check | ₹549 | ₹719 |
| Cost per vendor cleared (÷ 0.85) | ₹646 | ₹846 |
| Price | ₹999 | ₹999 |
| **Margin per vendor cleared** | **₹353 (35%)** | **₹153 (15%)** |

**Compared with a manual check:**

| Assumption | Value |
|---|---|
| Analyst cost to company | ₹8,00,000/year salary × 1.3 overheads = ₹10,40,000/year |
| Working hours | 1,760 a year, so ₹591/hour |
| Time per vendor | 75 min: registry lookup, website, director profiles, news search, write-up |
| Registry documents | ₹100 per company |
| **Manual cost per vendor checked** | **≈ ₹839** |
| **Manual cost per vendor cleared** (÷ 0.85) | **≈ ₹987** |

At ₹999, the tool costs about the same as an in-house analyst. What the client gets for it:

- **Speed:** about 1–2 minutes instead of a day's queue.
- **The same checks every time,** with a full evidence trail.
- **News screening with namesake clearing** that a manual search rarely does.
- **A person's approval is still kept.**

Margin rises as cost falls:

- **Fewer fetches:** page fetches are 63% of spend, so fewer article and website-page reads cut costs most.
- **Re-checks:** re-checks within the cache window are nearly free (0–3 credits measured).
- **Volume pricing:** a larger credit pack would lower the $0.30 per credit.

## Scaling

**100× the volume.**

- **Batch endpoints.** Crustdata has batch versions of company enrich, person enrich and person search,
  but they aren't on the current plan: they return 403. With them, the identify / profile / people
  steps for hundreds of vendors become a few async jobs. Without them, the sync endpoints already take
  up to 25 identifiers per call, so the steps can be batched across vendors.
- **Queue + workers.** Each request becomes a job row (it already is, in SQLite → Postgres). Steps go
  on a queue (Redis / SQS), and stateless workers run them. Steps are already idempotent and saved per
  job, so a worker crash just means another worker resumes the step.
- **Shared cache.** A shared cache (Redis / object storage) is keyed by the request hash, as now, with
  a lifetime per source: company records about 30 days, web pages about 7, DNS about 1 hour. The same
  carrier checked by two branches costs once.
- **Rate-limit pacing.** A shared token bucket per endpoint: Crustdata allows 30 requests per minute per
  endpoint, and GDELT one request per 5 s globally. Workers take a token before each call instead of
  being throttled with 429s. Page fetching is the bottleneck. New vendors measured 2–6 fetch calls
  each (about 4 on average; each call reads up to 10 pages), so one key clears roughly 7 vendors a
  minute (about 10,000 a day). Web search (2–4 calls per vendor) is the next limit. Beyond that, add
  keys or the batch plan.
- **Budget and time.** Each job keeps the per-run budget and deadline, and the cost ledger rolls up per
  client per month.

**A second client.**

- **Per-client config:** budget, time limit, cache lifetimes, risk keywords, and whether `possible_adverse`
  goes to review or is auto-rejected.
- **Per-client rules:** a versioned `decide` policy per client. Every output records which version
  decided it, so verdicts can be audited and re-run.
- **Per-client approvals:** the approvals table gets `client_id` and approver roles. One client's
  approval never clears a vendor for another.
- **Isolation:** `client_id` goes into the job ID, the output folder and the ledger. Only public source
  data (API responses, pages) is shared through the cache. Verdicts, approvals and notes are never
  shared between clients.

## Repository

| File | Role |
|---|---|
| `run.py` | The pipeline: steps, decide, output |
| `checks.py` | Pure matching rules: names, domains, triage, accusations, lookalikes, DNS answers |
| `crust_client.py` | Crustdata client: cache, retries, deadline, ledger, budget |
| `gdelt.py` | GDELT client: throttle, cooldown, cache |
| `dnscheck.py` | DNS-over-HTTPS lookup |
| `store.py` | Job store (Postgres in production, SQLite locally): jobs, steps, ledger, evidence, approvals, retries, results, API cache |
| `approve.py` | The human approval gate (`decide()`, shared by the CLI and the API) |
| `server.py` | FastAPI backend |
| `web/` | React + TypeScript frontend (Vite) |
| `vercel.json` | Vercel services and routing |
| `run_tests.py`, `tests/` | The test set and runner (`tests/mock_run.py` simulates outages offline) |
