# Architecture

One request becomes one **job** (ID = hash of vendor name + website domain). The job runs a fixed
set of **steps**. Each step is saved in SQLite only when it finishes, so a run that is killed or
stops at its budget or time limit resumes where it stopped. Every external call goes through a
client that caches, retries, logs cost and respects the run's deadline.

```mermaid
flowchart TD
    REQ(["Request<br/>run.py --name --website"]) --> JOB[("Job store · SQLite gatekeeper.db<br/>jobs · steps · evidence")]
    JOB --> S1

    subgraph PIPE["Steps (each saved when it finishes; a re-run skips finished steps)"]
        direction TB
        S1["1 · Identify<br/>company by domain and by name<br/>lookalike / borrowed-site check"]
        S2["2 · Profile<br/>status, founded → age, size, HQ"]
        S3["3 · Who runs it<br/>founders / CEO / directors<br/>current employer confirmed"]
        S4["4 · Website<br/>DNS: exists or NXDOMAIN<br/>names company, contact, business"]
        S5["5 · Operating signals<br/>employees, recent jobs, real operations"]
        S6a["6a · Adverse media: directors<br/>triage → read → namesake / mentioned / accused"]
        S6b["6b · Adverse media: company<br/>triage → read → accused / dispute / impersonated"]
        ER["Entity resolution<br/>match_confidence from domain, name,<br/>director employer, website, city"]
        D{"7 · Decide<br/>REAL_AND_TRADING · LOOKS_OFF · CANNOT_VERIFY"}

        S1 --> S2
        S2 --> S3
        S2 --> S4
        S3 --> S5
        S4 --> S5
        S3 --> S6a
        S2 --> S6b
        S5 --> ER
        S6a --> ER
        S6b --> ER
        ER --> D
    end

    D -->|REAL_AND_TRADING| GATE{{"Approval gate<br/>request filed as 'pending'"}}
    D -->|LOOKS_OFF / CANNOT_VERIFY| OUT
    GATE --> OUT(["Output · output/&lt;job_id&gt;.json<br/>verdict · summary · checks · red_flags<br/>evidence: source · field/passage · record date · retrieved_at"])
    HUMAN["approve.py &lt;job_id&gt;<br/>a person approves or rejects"] --> GATE

    subgraph DATA["Data calls (through the clients)"]
        direction TB
        CD["Crustdata API<br/>company identify / enrich<br/>person search / enrich<br/>job search<br/>web search / web fetch"]
        GD["GDELT DOC 2.0<br/>news, direct links, seendate"]
        DNS["DNS-over-HTTPS<br/>dns.google · cloudflare"]
    end

    subgraph CLIENT["crust_client.py · gdelt.py · dnscheck.py"]
        direction TB
        CACHE[("Response cache · cache/<br/>keyed by request hash<br/>atomic writes; failed pages not cached")]
        RETRY["Retries + deadline<br/>backoff on 429 / 5xx / timeout<br/>every attempt → retries table<br/>no retry past the run deadline"]
        LEDGER[("Cost ledger · ledger table<br/>credits from X-Credits-Used header")]
        BUDGET["Budget stop<br/>per-run credit limit"]
    end

    S1 & S2 & S3 & S4 & S5 & S6a & S6b -.->|every external call| CACHE
    CACHE -.->|miss| BUDGET
    BUDGET -.-> RETRY
    RETRY -.-> CD & GD & DNS
    RETRY -.->|response| LEDGER
    LEDGER -.-> CACHE

    classDef store fill:#e8ecff,stroke:#557,color:#111;
    classDef gate fill:#ffe9c7,stroke:#b77a00,color:#111;
    class JOB,CACHE,LEDGER store;
    class GATE,HUMAN gate;
```

## Where things sit

| Concern | Where | Notes |
|---|---|---|
| **Data calls** | Steps 1–6 → `crust_client.call`, `gdelt.search`, `dnscheck.lookup` | Every fact a step keeps becomes an evidence row with its source and retrieval time. |
| **Cache** | `cache/` (`crust_client._cache_path`, `gdelt._cache_path`) | A repeat call costs 0. Bot-check and failed pages are dropped from the cache so they get retried. |
| **Retries** | Inside each client | Every attempt and every give-up goes into the `retries` table and the output. The run deadline (limit − 15 s) caps any request or retry. GDELT gets 1 attempt, then is skipped for 15 min. |
| **Cost ledger** | `store.ledger`, written by `crust_client.call` | Credits actually charged, from `X-Credits-Used`. The output shows the job total and this run's spend. |
| **Budget stop** | `crust_client.call`, before each paid call | Over the per-run budget → the run stops, the verdict is `CANNOT_VERIFY`, and the next run resumes. |
| **Approvals** | `store.approvals`, `approve.py` | Only REAL_AND_TRADING files a pending request. Only a person (`approve.py`) clears a vendor. A changed verdict withdraws the request. |

## Parallelism

Steps that don't depend on each other run at the same time (3 workers), and their outputs are
merged back in pipeline order, so the result doesn't depend on which thread finished first:

- `who_runs_it` ∥ `website_check`
- `operating_signals` ∥ `adverse_media` ∥ `company_adverse_media`
- within the adverse-media steps: the leaders, the company searches and the page batches

## Decide, in order

1. Any unavailable source other than an unread adverse-media page → `CANNOT_VERIFY`. Unread
   adverse-media pages also give `CANNOT_VERIFY`, unless a page that *was* read already confirms a
   concrete negative.
2. Entity signals conflict → `CANNOT_VERIFY` (no company is picked).
3. A concrete negative, confirmed from a source we read → `LOOKS_OFF`. Concrete negatives are an
   accusing article about the company or a director, a lookalike domain, or a parked or default page.
4. Every check verified → `REAL_AND_TRADING` → approval gate.
5. Otherwise → `CANNOT_VERIFY`, with the list of what's missing.
