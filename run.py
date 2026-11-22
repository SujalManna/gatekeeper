"""Vendor check for the ContextCon Trust & compliance track.

    python run.py --name "<vendor name>" --website "<url>"

Job ID = hash of name + website domain, so running the same request again resumes:
finished steps come back from the store, API calls come back from the cache (0 credits).
Every fact in the output points at an evidence row: the endpoint or URL it came from, the
exact field or passage, the value, the record date (when the source has one) and when it was
retrieved. Nothing is filled in from model memory.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from urllib.parse import urlparse

import checks
import crust_client as cc
import dnscheck
import gdelt
import store

ACTION = "mark_vendor_cleared"
STEP_ORDER = ["identify", "company_profile", "who_runs_it", "website_check", "operating_signals",
              "adverse_media", "company_adverse_media", "entity_resolution", "decide", "approval"]
MAX_WORKERS = 3  # parallel calls; Crustdata's limit is 30 requests/min per endpoint, GDELT is locked to 1/5s
VERDICTS = ("REAL_AND_TRADING", "LOOKS_OFF", "CANNOT_VERIFY")


class StopRun(Exception):
    """Stop without saving the current step, so a re-run picks up from here."""


# ---------- plumbing ----------
def job_id_for(name, website):
    key = checks.norm_space(name).lower() + "|" + checks.domain_of(website)
    return "v_" + hashlib.sha256(key.encode()).hexdigest()[:12]


def new_out():
    return {"data": {}, "checks": [], "red_flags": [], "evidence": [], "unavailable": []}


def mark_unavailable(out, what, rec_or_reason, kind="source"):
    """A source we could not read (outage, timeout, error status). Never treated as 'no record'.
    kind='adverse_page': an adverse-media article that passed triage but couldn't be read. Such a
    page can only add negative evidence, never clear the vendor (see step_decide)."""
    if isinstance(rec_or_reason, str):
        reason = rec_or_reason
    elif rec_or_reason.get("unavailable_reason"):
        reason = rec_or_reason["unavailable_reason"]
    else:
        reason = f"HTTP {rec_or_reason.get('status_code')}: {json.dumps(rec_or_reason.get('error'))[:200]}"
    out["unavailable"].append({"source": what, "reason": reason, "kind": kind})


def ev(out, source, field, value, retrieved_at, record_date=None):
    out["evidence"].append({"source": source, "field_or_passage": field, "value": value,
                            "record_date": record_date, "retrieved_at": retrieved_at})


def check(out, name, status, detail):
    assert status in ("verified", "failed", "unclear", "not_checked")
    out["checks"].append({"check": name, "status": status, "detail": detail})


def flag(out, name, severity, detail):
    """severity 'concrete' = negative evidence that can drive LOOKS_OFF; 'warning' = gap or doubt."""
    out["red_flags"].append({"flag": name, "severity": severity, "detail": detail})


def api(endpoint, body, job):
    """Unavailable sources come back as a record with data=None; the step records them and carries on.
    Only auth/credit failures stop the run, since every later call would fail the same way."""
    rec = cc.call(endpoint, body, job)
    code = rec.get("status_code")
    if code in (401, 402):
        raise StopRun(f"{endpoint} returned {code}: {json.dumps(rec.get('error'))[:300]}")
    return rec


def src(endpoint, body):
    return f"crustdata {endpoint} {json.dumps(body, sort_keys=True)}"


def dig(d, *path):
    for p in path:
        if isinstance(d, dict):
            d = d.get(p)
        elif isinstance(d, list) and isinstance(p, int) and len(d) > p:
            d = d[p]
        else:
            return None
    return d


def first_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("results", "data", "items"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


# ---------- step 1: identify ----------
def _matches(rec):
    if rec.get("status_code") == 404:
        return []
    if rec.get("status_code") == 400 and "invalid company domains" in json.dumps(rec.get("error") or "").lower():
        return []  # Crustdata rejected the domain outright: a definitive "no record", not an outage
    if rec.get("data") is None:
        return None
    out = []
    for m in dig(first_list(rec["data"]), 0, "matches") or []:
        bi = dig(m, "company_data", "basic_info") or {}
        out.append({"id": dig(m, "company_data", "crustdata_company_id") or bi.get("crustdata_company_id"),
                    "name": bi.get("name"), "primary_domain": bi.get("primary_domain"),
                    "all_domains": bi.get("all_domains") or [], "confidence": m.get("confidence_score")})
    return out


def step_identify(job, name, domain):
    out = new_out()
    bd, bn = {"domains": [domain]}, {"names": [name]}
    rd, rn = api("/company/identify", bd, job), api("/company/identify", bn, job)
    dm, nm = _matches(rd), _matches(rn)
    for label, recd, body, ms in (("domain", rd, bd, dm), ("name", rn, bn, nm)):
        if ms is None:
            mark_unavailable(out, f"/company/identify by {label}", recd)
            check(out, f"identify_by_{label}", "unclear", "source unavailable (not the same as no match)")
            continue
        ev(out, src("/company/identify", body), "count(matches)", len(ms), recd["retrieved_at"])
        for i, m in enumerate(ms[:3]):
            p = f"[0].matches[{i}].company_data"
            ev(out, src("/company/identify", body), p + ".crustdata_company_id", m["id"], recd["retrieved_at"])
            ev(out, src("/company/identify", body), p + ".basic_info.name", m["name"], recd["retrieved_at"])
            ev(out, src("/company/identify", body), p + ".basic_info.primary_domain", m["primary_domain"],
               recd["retrieved_at"])
            ev(out, src("/company/identify", body), f"[0].matches[{i}].confidence_score", m["confidence"],
               recd["retrieved_at"])

    company_id = None
    top = (dm or [None])[0]
    name_ids = {m["id"] for m in (nm or [])}
    if top:
        exact_domain = domain in [d.lower() for d in top["all_domains"] + [top["primary_domain"] or ""]]
        sim = checks.name_similarity(name, top["name"])
        consistent = sim >= 85 or top["id"] in name_ids
        out["data"]["domain_company"] = {**top, "name_similarity": sim, "exact_domain": exact_domain,
                                         "in_name_search": top["id"] in name_ids}
        # A fuzzy domain match to a company whose real domain the given one imitates is a
        # typosquat, not that company: never adopt the company it copies.
        look, real, lsim = (False, None, 0) if exact_domain else checks.is_lookalike(
            domain, top["all_domains"] + [top["primary_domain"]])
        if look:
            out["data"]["domain_company"]["lookalike_of"] = real
            flag(out, "lookalike_domain", "concrete",
                 f"{domain} fuzzy-matched \"{top['name']}\", whose real domain is {real}; {domain} is a near-copy "
                 f"of it (label similarity {lsim})")
            check(out, "identify", "failed", "website looks like a copy of the matched company's domain")
        elif consistent:
            company_id = top["id"]
            check(out, "identify", "verified",
                  f"{domain} resolves to \"{top['name']}\" (crustdata id {top['id']}), which matches the "
                  f"name \"{name}\" (similarity {sim}{', also returned by the name search' if top['id'] in name_ids else ''})")
        else:
            # Which company does the *name* point to? If it's a different company with its own
            # domain, the site belongs to someone else. Otherwise it may just be a brand vs legal name.
            other = next((m for m in (nm or []) if m["id"] != top["id"]
                          and checks.name_similarity(name, m["name"]) >= 90), None)
            if other and exact_domain and (other["primary_domain"] or "").lower() != domain:
                # Organizer rule: conflicting entity signals -> CANNOT_VERIFY, never pick one.
                out["data"]["name_company"] = other
                flag(out, "site_belongs_to_other_company", "warning",
                     f"{domain} is the domain of \"{top['name']}\" (id {top['id']}), while \"{name}\" resolves to "
                     f"\"{other['name']}\" (id {other['id']}, domain {other['primary_domain']})")
                check(out, "identify", "failed", "website and name point to different companies")
            else:
                flag(out, "name_website_mismatch", "warning",
                     f"{domain} resolves to \"{top['name']}\" (similarity {sim} to \"{name}\"); "
                     "could be a brand vs legal name, not confirmed either way")
                check(out, "identify", "unclear", "website company name does not match the given name")
    else:
        if dm is not None:
            flag(out, "no_match_for_website", "warning", f"no Crustdata company for domain {domain}")
        strong = [m for m in (nm or []) if checks.name_similarity(name, m["name"]) >= 90]
        look = None
        for m in strong:
            is_look, real, lsim = checks.is_lookalike(domain, [m["primary_domain"]] + m["all_domains"])
            if is_look:
                look = (m, real, lsim)
                break
        if look:
            m, real, lsim = look
            flag(out, "lookalike_domain", "concrete",
                 f"\"{name}\" resolves to \"{m['name']}\" at {real}; the given site {domain} is a near-copy of "
                 f"that domain (label similarity {lsim})")
            check(out, "identify", "failed", "website looks like a copy of the named company's domain")
        elif strong:
            check(out, "identify", "unclear",
                  f"name matches {len(strong)} compan{'y' if len(strong) == 1 else 'ies'} but none is linked to {domain}")
        else:
            check(out, "identify", "failed" if nm == [] and dm == [] else "unclear",
                  "no Crustdata match for the website or the name")
            if nm == []:
                flag(out, "no_match_for_name", "warning", f"no Crustdata company named \"{name}\"")

    if nm:
        best = nm[0]["confidence"] or 0
        close = {m["id"] for m in nm if (m["confidence"] or 0) >= best - 0.05}
        if len(close) > 1:
            if company_id in close:
                check(out, "name_ambiguity", "verified",
                      f"{len(close)} close matches for \"{name}\"; the website resolves it to id {company_id}")
            else:
                flag(out, "multiple_close_matches", "warning",
                     f"{len(close)} companies match \"{name}\" about equally and the website doesn't pick one")
    out["data"]["company_id"] = company_id
    return out


# ---------- step 2: company profile ----------
def step_company_profile(job, company_id, domain):
    out = new_out()
    if not company_id:
        check(out, "company_profile", "not_checked", "no company identified")
        return out
    body = {"crustdata_company_ids": [company_id], "fields": ["basic_info", "locations", "updated_at"]}
    rec = api("/company/enrich", body, job)
    if rec.get("data") is None:
        mark_unavailable(out, "/company/enrich", rec)
        check(out, "company_profile", "unclear", "source unavailable")
        return out
    cd = dig(first_list(rec["data"]), 0, "matches", 0, "company_data") or {}
    bi, loc = cd.get("basic_info") or {}, cd.get("locations") or {}
    rdate, at, s = cd.get("updated_at"), rec["retrieved_at"], src("/company/enrich", body)
    p = "[0].matches[0].company_data."
    fields = {
        "name": ("basic_info.name", bi.get("name")),
        "status": ("basic_info.status", bi.get("status")),
        "year_founded": ("basic_info.year_founded", bi.get("year_founded")),
        "employee_count_range": ("basic_info.employee_count_range", bi.get("employee_count_range")),
        "company_type": ("basic_info.company_type", bi.get("company_type")),
        "website": ("basic_info.website", bi.get("website")),
        "primary_domain": ("basic_info.primary_domain", bi.get("primary_domain")),
        "profile_url": ("basic_info.professional_network_url", bi.get("professional_network_url")),
        "description": ("basic_info.description", bi.get("description")),
        "industries": ("basic_info.industries", bi.get("industries")),
        "hq": ("locations.headquarters", loc.get("headquarters")),
        "hq_street": ("locations.street_address", loc.get("street_address")),
        "hq_state": ("locations.state", loc.get("state")),
        "hq_country": ("locations.country", loc.get("country")),
    }
    data = {}
    for k, (f, v) in fields.items():
        data[k] = v
        if v not in (None, "", []):
            ev(out, s, p + f, v, at, rdate)
    data["legal_name"] = None
    data["legal_name_note"] = ("Crustdata has no legal-name field; basic_info.name is the company's "
                               "profile name, not a registry legal name")
    if data["year_founded"]:
        year = int(str(at)[:4])
        data["age_years"] = year - int(data["year_founded"])
        data["age_basis"] = f"{year} (retrieval year) - basic_info.year_founded {data['year_founded']}"
    else:
        data["age_years"] = None
    data["record_date"] = rdate
    out["data"] = data

    st = data["status"]
    if st == "active":
        check(out, "status", "verified", "basic_info.status = active")
    elif st == "deleted":
        check(out, "status", "failed", "basic_info.status = deleted")
        flag(out, "record_retired", "warning",
             "Crustdata status is 'deleted' (it retired this company record). That can mean a closed company "
             "or a merged duplicate record, so on its own it is not proof the vendor stopped trading")
    else:
        check(out, "status", "unclear", f"basic_info.status = {st!r}")
    if not data["year_founded"]:
        check(out, "age", "unclear", "basic_info.year_founded is empty")
    else:
        check(out, "age", "verified", f"founded {data['year_founded']} ({data['age_years']} years)")
    doms = [d.lower() for d in [bi.get("primary_domain") or ""] + (bi.get("all_domains") or [])]
    if domain in doms:
        check(out, "profile_domain", "verified", f"{domain} is listed on the company profile")
    else:
        check(out, "profile_domain", "unclear", f"{domain} not in the profile's domains {doms}")
    out["data"]["verified"] = st == "active" and bool(data["name"]) and bool(data["year_founded"])
    return out


# ---------- step 3: who runs it ----------
def _role_rank(title):
    """founder > CEO/MD/chair > board-style director > other C-level > owner/proprietor/partner.
    Owner titles rank last: on large companies they are usually stray self-reported profiles,
    while for small proprietorships they are still picked when nothing better exists."""
    t = (title or "").lower()
    if "founder" in t:
        return 0
    if re.search(r"\b(ceo|chief executive|managing director|chairman|chairperson|md)\b", t):
        return 1
    # Board-style only: "Director", "Executive/Whole-time/Independent/... Director". Functional titles
    # such as "Director Compensation" or "Director of Sales" are not who runs the company.
    if re.fullmatch(r"(?:(?:executive|whole[- ]?time|non[- ]executive|independent|board|additional|nominee)\s+)?"
                    r"director(?:\s*(?:&|and)\s*(?:co-?founder|ceo|owner|promoter))?", t.strip()):
        return 2
    if t.startswith("chief ") or re.search(r"\b(cto|coo|cfo)\b", t):
        return 3
    if re.search(r"\b(proprietor|owner|partner)\b", t):
        return 4
    return 9


def _city(loc):
    if isinstance(loc, dict):
        return loc.get("city") or (loc.get("full_location") or "").split(",")[0].strip() or None
    if isinstance(loc, str) and loc.strip():
        return loc.split(",")[0].strip()
    return None


def _current_at(person_data, company_id):
    cur = dig(person_data, "experience", "employment_details", "current") or []
    here = [c for c in cur if c.get("crustdata_company_id") == company_id]
    return here, cur


def step_who_runs_it(job, company_id):
    out = new_out()
    out["data"]["people"] = []
    if not company_id:
        check(out, "people", "not_checked", "no company identified")
        return out
    title_terms = ["founder", "co-founder", "CEO", "Chief Executive Officer", "Managing Director", "Director"]
    body = {
        "filters": {"op": "and", "conditions": [
            {"field": "experience.employment_details.current.company_id", "type": "=", "value": company_id},
            {"op": "or", "conditions": [
                *[{"field": "experience.employment_details.current.title", "type": "[.]", "value": t}
                  for t in title_terms],
                {"field": "experience.employment_details.current.seniority_level", "type": "in",
                 "value": ["CXO", "Owner / Partner"]},
            ]},
        ]},
        "fields": ["crustdata_person_id", "basic_profile.name", "basic_profile.current_title",
                   "basic_profile.location", "experience.employment_details.current",
                   "social_handles.professional_network_identifier.profile_url"],
        "limit": 25,
    }
    rec = api("/person/search", body, job)
    if rec.get("data") is None:
        mark_unavailable(out, "/person/search (leaders)", rec)
        check(out, "people", "unclear", "source unavailable")
        return out
    s, at = src("/person/search", {"company_id": company_id, "titles": title_terms}), rec["retrieved_at"]
    profiles = (rec["data"] or {}).get("profiles") or []
    ev(out, s, "total_count", (rec["data"] or {}).get("total_count"), at)
    cands = []
    for i, pr in enumerate(profiles):
        here, _ = _current_at(pr, company_id)
        title = (here[0].get("title") if here else None) or dig(pr, "basic_profile", "current_title")
        url = dig(pr, "social_handles", "professional_network_identifier", "profile_url")
        rank = _role_rank(title)
        if rank < 9 and url:
            cands.append((rank, i, {"name": dig(pr, "basic_profile", "name"), "title": title, "profile_url": url,
                                    "crustdata_person_id": pr.get("crustdata_person_id"),
                                    "city": _city(dig(pr, "basic_profile", "location")), "search_index": i}))
    # Deterministic: (role rank, has full name, name). Search result order can change between
    # runs, so it is never used; two runs on the same data always pick the same people.
    cands.sort(key=lambda c: (c[0], checks.is_abbreviated(c[2]["name"]),
                              checks.norm_space(c[2]["name"]).lower(), c[2]["profile_url"]))
    top, seen = [], set()
    for _, _, p in cands:  # one person can have two profiles; count them once
        key = checks.norm_space(p["name"]).lower()
        if key not in seen:
            seen.add(key)
            top.append(p)
        if len(top) == 2:
            break
    if not top:
        check(out, "people", "failed" if profiles == [] else "unclear",
              f"person search found {len(profiles)} senior profile(s) at id {company_id}, none a founder, CEO or director")
        flag(out, "no_leadership_found", "warning", "no founder, CEO or director found for this company")
        return out
    for p in top:
        i = p["search_index"]
        ev(out, s, f"profiles[{i}].basic_profile.name", p["name"], at)
        ev(out, s, f"profiles[{i}].experience.employment_details.current[].title", p["title"], at)

    eb = {"professional_network_profile_urls": [p["profile_url"] for p in top],
          "fields": ["basic_profile", "experience", "social_handles"]}
    er = api("/person/enrich", eb, job)
    if er.get("data") is None:
        mark_unavailable(out, "/person/enrich", er)
    rows = first_list(er.get("data")) if er.get("data") is not None else []
    by_url = {r.get("matched_on"): r for r in rows}
    es, eat = src("/person/enrich", eb), er["retrieved_at"]
    confirmed = 0
    for p in top:
        r = by_url.get(p["profile_url"]) or {}
        pd = dig(r, "matches", 0, "person_data")
        person = dict(p)
        person["match_status"] = r.get("match_status") or ("error" if er.get("data") is None else "missing")
        if not pd:
            person["employer_match"] = None
            check(out, f"employer:{p['name']}", "unclear",
                  f"person enrich match_status = {person['match_status']}; current employer not confirmed")
            out["data"]["people"].append(person)
            continue
        here, cur = _current_at(pd, company_id)
        person["city"] = _city(dig(pd, "basic_profile", "location")) or p["city"]
        if here:
            person["title"] = here[0].get("title") or p["title"]
            person["employer_match"] = True
            person["current_start_date"] = here[0].get("start_date")
            confirmed += 1
            idx = cur.index(here[0])
            base = f"[matched_on={p['profile_url']}].matches[0].person_data.experience.employment_details.current[{idx}]"
            ev(out, es, base + ".crustdata_company_id", here[0].get("crustdata_company_id"), eat)
            ev(out, es, base + ".name", here[0].get("name"), eat)
            ev(out, es, base + ".title", here[0].get("title"), eat)
            if here[0].get("start_date"):
                ev(out, es, base + ".start_date", here[0].get("start_date"), eat)
            check(out, f"employer:{p['name']}", "verified",
                  f"current role \"{person['title']}\" at crustdata id {company_id}")
        else:
            person["employer_match"] = False
            names = [c.get("name") for c in cur]
            ev(out, es, f"[matched_on={p['profile_url']}].matches[0].person_data.experience.employment_details.current[].name",
               names, eat)
            check(out, f"employer:{p['name']}", "failed", f"current employers on profile: {names}")
            flag(out, "director_employer_mismatch", "warning",
                 f"{p['name']}'s enriched profile lists current employer(s) {names}, not this company")
        if person["city"]:
            ev(out, es, f"[matched_on={p['profile_url']}].matches[0].person_data.basic_profile.location",
               dig(pd, "basic_profile", "location"), eat)
        out["data"]["people"].append(person)
    check(out, "people", "verified" if confirmed else "unclear",
          f"{confirmed} of {len(top)} leaders confirmed as currently at this company")
    out["data"]["verified"] = confirmed > 0
    return out


# ---------- step 4: website ----------
MAX_HUMAN_MODE_PER_RUN = 8   # browser-like fetches are slow; past this, pages count as not read
_HUMAN = {"used": 0}
_HUMAN_LOCK = threading.Lock()


def _fetch(urls, job, human=False):
    body = {"urls": urls}
    if human:
        body["human_mode"] = True
        if not cc.cache_exists(cc._cache_path("/web/enrich/live", body)):  # cached answers are free and fast
            with _HUMAN_LOCK:
                allowed = max(0, MAX_HUMAN_MODE_PER_RUN - _HUMAN["used"])
                take = urls[:allowed]
                _HUMAN["used"] += len(take)
            if len(take) < len(urls):
                skipped = [{"success": False, "url": u, "skipped": f"human_mode cap of {MAX_HUMAN_MODE_PER_RUN} per run"}
                           for u in urls[len(take):]]
                if not take:
                    return {"data": skipped, "retrieved_at": None, "human_mode_capped": True}, body, skipped
                rec, b, items = _fetch(take, job, human=True)
                return rec, b, list(items) + skipped
    rec = api("/web/enrich/live", body, job)
    items = first_list(rec.get("data")) if rec.get("data") is not None else []
    def unread(i):
        return not i.get("success") or checks.is_interstitial(
            i.get("title"), checks.html_to_text(i.get("content") or "")[0])
    if rec.get("data") is not None and (len(items) < len(urls) or any(unread(i) for i in items)):
        # A page that failed to load or hit a bot check is "unavailable", not "empty":
        # drop it from the cache so a re-run retries.
        cc.cache_drop(cc._cache_path("/web/enrich/live", body))
    return rec, body, items


def step_website_check(job, website, name, profile):
    out = new_out()
    url = checks.normalize_url(website)

    # Does the domain exist at all? NXDOMAIN is a definitive "no record" (not an outage) and
    # needs no fetch; a DNS timeout is "unavailable" and never read as "no such domain".
    host = urlparse(url).netloc.split("@")[-1].split(":")[0].lower()
    dns = dnscheck.lookup(host, job)
    if dns["status"] == "nxdomain" and host.startswith("www."):
        bare = dnscheck.lookup(host[4:], job)
        if bare["status"] != "nxdomain":
            dns = bare
    out["data"]["dns"] = dns
    if dns["status"] in ("exists", "nxdomain"):
        ev(out, f"DNS-over-HTTPS {dns['resolver']} name={dns['host']} type=A", "Status (RCODE)",
           f"{dns['rcode']} ({'NOERROR: domain exists' if dns['status'] == 'exists' else 'NXDOMAIN: no such domain'})",
           dns["retrieved_at"])
    if dns["status"] == "nxdomain":
        flag(out, "website_does_not_exist", "warning",
             f"{dns['host']} has no DNS record (NXDOMAIN): the website given does not exist")
        check(out, "website_fetch", "failed", f"{dns['host']} does not exist (NXDOMAIN); nothing to fetch")
        out["data"].update({"fetched": False, "nxdomain": True, "verified": False})
        return out

    rec, body, items = _fetch([url], job)
    item = items[0] if items else {}
    text, links = checks.html_to_text(item.get("content") or "")
    blocked = checks.is_interstitial(item.get("title"), text)
    if not item.get("success") or len(text) < 300 or blocked:
        rec2, body2, items2 = _fetch([url], job, human=True)
        item2 = items2[0] if items2 else {}
        text2, links2 = checks.html_to_text(item2.get("content") or "")
        blocked2 = checks.is_interstitial(item2.get("title"), text2)
        if item2.get("success") and not blocked2 and (blocked or len(text2) > len(text)):
            rec, body, item, text, links, blocked = rec2, body2, item2, text2, links2, False
    if blocked:
        item = {**item, "success": False}  # a bot-check page is not the vendor's site
    at, title = rec["retrieved_at"], item.get("title") or ""
    s = f"{item.get('url') or url} (via crustdata /web/enrich/live)"
    out["data"].update({"fetched": bool(item.get("success")), "title": title, "text_chars": len(text)})
    if not item.get("success"):
        if dns["status"] == "unavailable":
            mark_unavailable(out, f"DNS lookup for {host}", dns["reason"])
        mark_unavailable(out, f"vendor website {url}",
                         rec if rec.get("data") is None else "page fetch returned success=false (also tried human_mode)")
        check(out, "website_fetch", "unclear", f"could not fetch {url}; unavailable, not proof the site is missing")
        out["data"]["verified"] = False
        return out
    # The page loaded, so the domain exists; a DNS resolver that was down changes nothing.
    ev(out, s, "title", title, at)
    low = text.lower()

    parked, i = checks.find_first(checks.PARKED, low)
    default, j = checks.find_first(checks.DEFAULT_PAGES, low)
    if parked and len(text) < 3000:
        ev(out, s, "passage", checks.passage(text, i), at)
        flag(out, "parked_domain", "concrete", f"site text says \"{parked}\"")
        check(out, "website_real", "failed", "parked / for-sale domain")
    elif default and len(text) < 2000:
        ev(out, s, "passage", checks.passage(text, j), at)
        flag(out, "default_server_page", "concrete", f"site shows a server default page (\"{default}\")")
        check(out, "website_real", "failed", "default server page, no business content")

    tmpl, k = checks.find_first(checks.TEMPLATE, low)
    if tmpl:
        ev(out, s, "passage", checks.passage(text, k), at)
        flag(out, "template_text", "warning", f"site contains template text \"{tmpl}\"")
    if len(text) < 300:
        flag(out, "thin_or_empty_site", "warning",
             f"only {len(text)} characters of readable text (could be a JS-only site, so not treated as proof)")

    extra = checks.same_site_links(item.get("url") or url, links)
    if extra:
        r3, b3, it3 = _fetch(extra, job)
        for it in it3:
            if it.get("success"):
                t3, _ = checks.html_to_text(it.get("content") or "")
                if not checks.is_interstitial(it.get("title"), t3):
                    text += "\n" + t3
        out["data"]["extra_pages"] = extra
    low = text.lower()

    # 1. names the company
    names = [name] + ([profile.get("name")] if profile.get("name") else [])
    named = None
    for n in names:
        nn = checks.norm_name(n)
        flat = " " + " ".join(checks.norm_name(low + " " + title.lower()).split()) + " "
        if nn and f" {nn} " in flat:
            named = n
            break
    if named:
        pos = low.find(checks.norm_name(named).split()[0])
        ev(out, s, "passage (company name)", checks.passage(text, max(pos, 0)) if pos >= 0 else title, at)
        check(out, "website_names_company", "verified", f"site names \"{named}\"")
    else:
        check(out, "website_names_company", "failed", f"site text never names {names}")

    # 2. real address or contact
    hq_terms = []
    for f in ("hq_street", "hq"):
        v = profile.get(f)
        if v:
            hq_terms += [v] + [part.strip() for part in str(v).split(",")[:2]]
    addr, ai = checks.find_address(text, hq_terms)
    out["data"]["address_matches_hq"] = bool(addr) and addr in hq_terms
    out["data"]["names_company"] = named
    em = checks.EMAIL_RE.search(text)
    ph = checks.find_phone(text)
    found = []
    if addr:
        ev(out, s, "passage (address)", checks.passage(text, ai), at)
        found.append(f"address ({addr})")
    if em:
        ev(out, s, "passage (email)", em.group(), at)
        found.append(f"email {em.group()}")
    if ph:
        ev(out, s, "passage (phone)", checks.norm_space(ph.group()), at)
        found.append(f"phone {checks.norm_space(ph.group())}")
    check(out, "website_contact", "verified" if found else "failed",
          ("lists " + ", ".join(found)) if found else "no address, email or phone found on the pages fetched")

    # 3. describes the business the profile says it does
    claimed = checks.keywords(" ".join([profile.get("description") or ""] + (profile.get("industries") or [])))
    claimed -= checks.keywords(" ".join(names))
    overlap = sorted(claimed & checks.keywords(text))
    if not claimed:
        check(out, "website_describes_business", "unclear", "no Crustdata description to compare against")
    elif len(overlap) >= 3:
        ev(out, s, "keywords shared with basic_info.description/industries", overlap[:15], at)
        check(out, "website_describes_business", "verified",
              f"site describes the same business as the profile ({', '.join(overlap[:8])})")
    else:
        check(out, "website_describes_business", "unclear",
              f"only {len(overlap)} profile keywords found on the site ({', '.join(overlap)})")

    out["data"]["verified"] = (not any(f["severity"] == "concrete" for f in out["red_flags"])
                               and all(c["status"] == "verified" for c in out["checks"]
                                       if c["check"] in ("website_names_company", "website_contact",
                                                         "website_describes_business")))
    return out


# ---------- step 5: adverse media ----------
def step_adverse_media(job, people, company_names, domain):
    out = new_out()
    out["data"]["hits"] = []
    if not people:
        check(out, "adverse_media", "not_checked", "no leaders identified to search")
        out["data"]["complete"] = False
        return out
    out["data"]["sources_degraded"] = []
    # Each leader is screened independently, so screen them at the same time; results are merged
    # back in the leaders' order so the output is the same however the threads finish.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(lambda p: _screen_person(job, p, company_names, domain), people))
    complete, screened = True, 0
    for sub, ok, did in results:
        for k in ("checks", "red_flags", "evidence", "unavailable"):
            out[k] += sub[k]
        out["data"]["hits"] += sub["data"]["hits"]
        out["data"]["sources_degraded"] += sub["data"]["sources_degraded"]
        complete &= ok
        screened += did
    out["data"]["complete"] = complete
    out["data"]["leaders_screened"] = screened
    return out


def _screen_person(job, person, company_names, domain):
    """Adverse-media screening for one leader. Returns (its own step output, complete?, screened 0/1)."""
    out = new_out()
    out["data"].update({"hits": [], "sources_degraded": []})
    complete = True
    name = person["name"]
    if checks.is_abbreviated(name):
        # The surname is hidden at the source, so no search can find or clear this person.
        # Listed as a gap; the verdict still needs at least one fully screened leader.
        check(out, f"adverse:{name}", "unclear",
              f"name is abbreviated on record (\"{name}\"), so news search can't screen them")
        flag(out, "leader_not_screenable", "warning",
             f"{name}: surname hidden on the profile, so adverse media was not searched for this person")
        return out, True, 0

    # 1. Find hits: GDELT first (direct article links + seendate); Crustdata web search only if
    # GDELT fails (after one failure GDELT is skipped for a cooldown, see gdelt.py).
    hits, source = _adverse_hits_gdelt(job, name, company_names, out)
    if hits is None:
        hits, source = _adverse_hits_crustdata(job, name, out)
    if hits is None:
        check(out, f"adverse:{name}", "unclear", "GDELT and Crustdata web search both unavailable")
        return out, False, 0
    hits = [h for h in hits if "linkedin.com/in/" not in h["url"].lower()
            and checks.domain_of(h["url"]) != domain]

    # 2. Triage on title/snippet before fetching anything.
    to_read = []
    for h in hits:
        needs, why = checks.triage(f"{h['title'] or ''}\n{h.get('snippet') or ''}", name, company_names)
        h.update({"person": name, "triage": why})
        if needs:
            to_read.append(h)
        else:
            h.update({"verdict": "not_relevant", "reason": why, "basis": "title/snippet triage",
                      "passage": checks.norm_space(f"{h['title'] or ''} — {h.get('snippet') or ''}")})
    # adverse-keyword hits first, then company-only ones; deterministic order
    to_read.sort(key=lambda h: ("adverse term" not in h["triage"], h["url"]))
    overflow = to_read[MAX_READ_PER_PERSON:]
    to_read = to_read[:MAX_READ_PER_PERSON]

    # 3. Read the hits that passed triage.
    pages = _read_pages([h["url"] for h in to_read], job, out)
    for h in to_read:
        it, fat = pages.get(h["url"], (None, None))
        text = ""
        if it and it.get("success"):
            text, _ = checks.html_to_text(it.get("content") or "")
            if checks.is_interstitial(it.get("title"), text):
                text = ""
        if text and not checks.name_positions((it.get("title") or "") + "\n" + text, name):
            # The search engine saw the full name here, but the page we got doesn't show it
            # (login wall, consent page, different landing page): we haven't read what was found.
            text = ""
        if text:
            verdict, reason, psg = checks.classify_hit((it.get("title") or "") + "\n" + text, person,
                                                       company_names)
            h.update({"verdict": verdict, "reason": reason, "basis": "page", "passage": psg,
                      "page_title": it.get("title"), "read_at": fat})
        elif checks.is_social(h["url"], (it or {}).get("title"), h["title"]):
            if not _judge_social_wall(job, h, person, company_names, out):
                complete = False
        else:
            complete = False
            mark_unavailable(out, f"adverse-media page {h['url']}",
                             "passed triage but could not be read (bot check or fetch failure)", "adverse_page")
            h.update({"verdict": "unreviewed", "basis": "title/snippet only",
                      "passage": checks.norm_space(f"{h['title'] or ''} — {h.get('snippet') or ''}"),
                      "reason": "passed triage (" + h["triage"] + ") but the page could not be read, or the "
                                "page we got does not show the name; not cleared"})
    for h in overflow:
        complete = False
        h.update({"verdict": "unreviewed", "basis": "title/snippet only", "passage": h["triage"],
                  "reason": f"passed triage but over the {MAX_READ_PER_PERSON}-page reading cap; not cleared"})
        mark_unavailable(out, f"adverse-media page {h['url']}", "over the reading cap", "adverse_page")

    # 4. Record every hit with its evidence.
    for h in hits:
        out["data"]["hits"].append({k: h.get(k) for k in ("person", "source", "url", "title", "record_date",
                                                           "verdict", "reason", "basis", "passage")})
        if h.get("passage"):
            where = h.get("evidence_source") or (
                f"{h['url']} (via crustdata /web/enrich/live)" if h["basis"] == "page" else h["found_via"])
            ev(out, where, "passage" if h["basis"] in ("page", "readable copy") else "title/snippet",
               h["passage"], h.get("read_at") or h["found_at"], h.get("record_date"))
        if h["verdict"] == "adverse":
            flag(out, "director_adverse_media", "concrete", f"{name}: {h['url']} — {h['reason']}")
        elif h["verdict"] == "possible_adverse":
            flag(out, "possible_director_adverse_media", "warning", f"{name}: {h['url']} — {h['reason']}")
    counts = {}
    for h in hits:
        counts[h["verdict"]] = counts.get(h["verdict"], 0) + 1
    bad = counts.get("adverse")
    unsure = counts.get("possible_adverse", 0) + counts.get("unreviewed", 0)
    check(out, f"adverse:{name}", "failed" if bad else "unclear" if unsure else "verified",
          f"{len(hits)} hits via {source}: "
          + (", ".join(f"{n} {v}" for v, n in sorted(counts.items())) or "none"))
    return out, complete, 1


MAX_READ_PER_PERSON = 10
ADVERSE_QUERY_TERMS = ["fraud", "arrested", "scam", "lawsuit", '"shell company"']
COMPANY_QUERY_TERMS = ["fraud", "arrested", "scam", '"shell company"', "cheating", "booked", '"money laundering"']
MAX_READ_PER_COMPANY = 10


def _query_name(name):
    """'Grain Garden Delight Pvt Ltd' -> 'Grain Garden Delight' (articles often drop the suffix)."""
    words = re.sub(r"[^\w\s&.-]", " ", name or "").split()
    return " ".join(w for w in words if w.lower().strip(".") not in checks.LEGAL_SUFFIXES).strip()


def step_company_adverse_media(job, company_names, domain):
    """Adverse media about the company itself. Works even when Crustdata has no record of it,
    which is how a front with no profile and no known directors gets caught with evidence.
    A confirmed accusing article -> concrete flag 'company_adverse_media' (LOOKS_OFF)."""
    out = new_out()
    d = out["data"]
    d.update({"hits": [], "sources_degraded": [], "complete": True})
    qnames = []
    for n in company_names:
        q = _query_name(n)
        if q and checks.norm_name(q) not in [checks.norm_name(x) for x in qnames]:
            qnames.append(q)
    if not qnames:
        check(out, "company_adverse_media", "not_checked", "no company name to search")
        d["complete"] = False
        return out

    # Both sources, every time: GDELT returning nothing is not proof of no coverage (it holds about
    # three months of mostly large outlets), so Crustdata web search always runs too.
    def search_one(qn, which):
        """One search (GDELT or Crustdata) for one company name, into its own output (merged in order)."""
        sub = new_out()
        sub["data"].update({"hits": [], "sources": [], "degraded": [], "complete": True})
        if which == "gdelt":
            q = f'"{qn}" (' + " OR ".join(COMPANY_QUERY_TERMS) + ")"
            rec = gdelt.search(q, job)
            if rec.get("data") is None:
                sub["data"]["degraded"].append({"source": "GDELT", "company": qn, "status": "source unavailable",
                                                "reason": rec.get("unavailable_reason"),
                                                "note": "Crustdata web search still covers this company"})
                return sub
            arts = (rec["data"] or {}).get("articles") or []
            fv = f"gdelt doc api query={q!r}"
            ev(sub, fv, "count(articles)", len(arts), rec["retrieved_at"])
            sub["data"]["hits"] = [{"source": "gdelt", "url": a.get("url"), "title": a.get("title"), "snippet": None,
                                    "record_date": gdelt.seendate_iso(a.get("seendate")), "found_via": fv,
                                    "found_at": rec["retrieved_at"]} for a in arts if a.get("url")]
            sub["data"]["sources"].append(f"GDELT ({len(arts)})")
            return sub
        cq = f'"{qn}" ' + " OR ".join(COMPANY_QUERY_TERMS)
        body = {"query": cq, "sources": ["web", "news"]}
        crec = api("/web/search/live", body, job)
        if crec.get("data") is None:
            mark_unavailable(sub, f"/web/search/live for company \"{qn}\"", crec)
            sub["data"]["complete"] = False
            return sub
        s = src("/web/search/live", body)
        res = [r for r in ((crec["data"] or {}).get("results") or []) if r.get("url")]
        res.sort(key=lambda r: (int(r.get("position") or 99), r["url"]))
        ev(sub, s, "count(results)", len(res), crec["retrieved_at"])
        sub["data"]["hits"] = [{"source": "crustdata_web_search", "url": r["url"], "title": r.get("title"),
                                "snippet": r.get("snippet"), "record_date": None, "found_via": s,
                                "found_at": crec["retrieved_at"]} for r in res]
        sub["data"]["sources"].append(f"Crustdata web search ({len(res)})")
        return sub

    # Every (name, source) search is independent: run them together, merge in a fixed order.
    tasks = [(qn, w) for qn in qnames[:2] for w in ("gdelt", "crustdata")]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        subs = list(pool.map(lambda t: search_one(*t), tasks))
    hits, sources = [], []
    for sub in subs:
        for k in ("evidence", "unavailable"):
            out[k] += sub[k]
        hits += sub["data"]["hits"]
        sources += sub["data"]["sources"]
        d["sources_degraded"] += sub["data"]["degraded"]
        d["complete"] &= sub["data"]["complete"]

    seen, uniq = set(), []
    for h in hits:
        if h["url"] not in seen and checks.domain_of(h["url"]) != domain:
            seen.add(h["url"])
            uniq.append(h)
    hits = uniq

    to_read = []
    for h in hits:
        needs, why = checks.company_triage(f"{h['title'] or ''}\n{h.get('snippet') or ''}", company_names)
        h["triage"] = why
        if needs:
            # What does the title/snippet itself say? Used to read likely accusations first, and to
            # judge a page that then can't be read.
            sv, sr, _ = checks.classify_company_hit(f"{h['title'] or ''}\n{h.get('snippet') or ''}", company_names)
            h.update({"snippet_verdict": sv, "snippet_reason": sr})
            to_read.append(h)
        else:
            h.update({"verdict": "not_relevant", "reason": why, "basis": "title/snippet triage",
                      "passage": checks.norm_space(f"{h['title'] or ''} — {h.get('snippet') or ''}")})
    rank = {"adverse": 0, "possible_adverse": 1}
    to_read.sort(key=lambda h: (rank.get(h["snippet_verdict"], 2), h["url"]))
    overflow, to_read = to_read[MAX_READ_PER_COMPANY:], to_read[:MAX_READ_PER_COMPANY]

    def judge_unread(h, why_unread):
        """A triage-passed page we didn't read: it blocks only if its own title/snippet already
        shows accusing wording next to the company's name."""
        snip = checks.norm_space(f"{h['title'] or ''} — {h.get('snippet') or ''}")
        if h["snippet_verdict"] in ("adverse", "possible_adverse"):
            d["complete"] = False
            mark_unavailable(out, f"company adverse-media page {h['url']}", why_unread, "adverse_page")
            h.update({"verdict": "unreviewed", "basis": "title/snippet only", "passage": snip,
                      "reason": f"{why_unread}; the snippet itself accuses the company ({h['snippet_reason']}), "
                                "so it is not cleared"})
        else:
            h.update({"verdict": "not_read_no_accusation_in_snippet", "basis": "title/snippet only",
                      "passage": snip,
                      "reason": f"{why_unread}; its title/snippet shows no accusation of the company "
                                f"({h['snippet_reason']}), so it does not block"})

    pages = _read_pages([h["url"] for h in to_read], job, out)
    for h in to_read:
        it, fat = pages.get(h["url"], (None, None))
        text = ""
        if it and it.get("success"):
            text, _ = checks.html_to_text(it.get("content") or "")
            if checks.is_interstitial(it.get("title"), text):
                text = ""
        full = ((it or {}).get("title") or "") + "\n" + text
        if text and not any(p.search(full) for _, p in checks._company_patterns(company_names)):
            text = ""  # the page we got doesn't show the company the search saw: not actually read
        if text:
            verdict, reason, psg = checks.classify_company_hit(full, company_names)
            h.update({"verdict": verdict, "reason": reason, "basis": "page", "passage": psg,
                      "page_title": it.get("title"), "read_at": fat})
        else:
            judge_unread(h, "passed triage but the page could not be read (bot check, login wall or fetch failure)")
    for h in overflow:
        judge_unread(h, f"passed triage but over the {MAX_READ_PER_COMPANY}-page reading cap")
    unread_ok = [h for h in to_read + overflow if h.get("verdict") == "not_read_no_accusation_in_snippet"]
    if unread_ok:
        flag(out, "company_pages_not_read", "note",
             f"{len(unread_ok)} page(s) about the company passed triage but were not read; none of their "
             "titles/snippets accuses the company, so they don't block. URLs: "
             + " ".join(h["url"] for h in unread_ok))

    for h in hits:
        d["hits"].append({k: h.get(k) for k in ("source", "url", "title", "page_title", "record_date",
                                               "verdict", "reason", "basis", "passage")})
        if h.get("passage"):
            where = (f"{h['url']} (page title: {h.get('page_title')!r}, via crustdata /web/enrich/live)"
                     if h["basis"] == "page" else h["found_via"])
            ev(out, where, "passage" if h["basis"] == "page" else "title/snippet",
               h["passage"], h.get("read_at") or h["found_at"], h.get("record_date"))
        if h["verdict"] == "adverse":
            flag(out, "company_adverse_media", "concrete",
                 f"article accuses the company itself: {h['url']} (\"{h.get('page_title') or h['title']}\") — {h['reason']}")
        elif h["verdict"] == "possible_adverse":
            flag(out, "company_possible_adverse", "warning",
                 f"article puts the company in a dispute or case (not fraud/crime): {h['url']} "
                 f"(\"{h.get('page_title') or h['title']}\") — {h['reason']}")
    counts = {}
    for h in hits:
        counts[h["verdict"]] = counts.get(h["verdict"], 0) + 1
    status = ("failed" if counts.get("adverse") else
              "unclear" if counts.get("unreviewed") or counts.get("possible_adverse") or not d["complete"]
              else "verified")
    check(out, "company_adverse_media", status,
          f"{len(hits)} hits via {', '.join(sources) or 'no source'}: "
          + (", ".join(f"{n} {v}" for v, n in sorted(counts.items())) or "none"))
    return out


def _adverse_hits_gdelt(job, name, company_names, out):
    """GDELT DOC search for the person together with the company name. None = GDELT unavailable."""
    cos = sorted({re.sub(r"[^\w\s.-]", " ", c).strip() for c in company_names if c})
    terms = ADVERSE_QUERY_TERMS + [f'"{c}"' if " " in c else c for c in cos]
    q = f'"{name}" (' + " OR ".join(terms) + ")"
    rec = gdelt.search(q, job)
    if rec.get("data") is None:
        out["data"]["sources_degraded"].append(
            {"source": "GDELT", "person": name, "status": "source unavailable",
             "reason": rec.get("unavailable_reason"),
             "note": "fell back to Crustdata web search; the check was completed there"})
        return None, None
    arts = (rec["data"] or {}).get("articles") or []
    found_via = f"gdelt doc api query={q!r}"
    ev(out, found_via, "count(articles)", len(arts), rec["retrieved_at"])
    return [{"source": "gdelt", "url": a.get("url"), "title": a.get("title"), "snippet": None,
             "record_date": gdelt.seendate_iso(a.get("seendate")), "found_via": found_via,
             "found_at": rec["retrieved_at"]} for a in arts if a.get("url")], "GDELT"


def _judge_social_wall(job, h, person, company_names, out):
    """A Facebook / Instagram / LinkedIn / X hit we can't read past the login wall.

    1. Search the headline (or the snippet's key phrase) for a readable copy of the same story,
       and judge from the first copy that names the person.
    2. If there is none, judge from the snippet with the same rule; a namesake result there is
       'namesake_unconfirmed', which doesn't block but is listed as a note with snippet and URL.
    Returns False only when the copy search itself was unavailable (then the hit stays unreviewed).
    """
    name = person["name"]
    phrases = checks.copy_search_phrases(h["title"], h.get("snippet"))
    phrase = " / ".join(phrases) or "(no usable phrase)"
    for ph in phrases:
        body = {"query": f"\"{ph}\"", "sources": ["news", "web"]}
        rec = api("/web/search/live", body, job)
        if rec.get("data") is None:
            mark_unavailable(out, f"/web/search/live for a readable copy of {h['url']}", rec)
            h.update({"verdict": "unreviewed", "basis": "title/snippet only",
                      "passage": checks.norm_space(f"{h['title'] or ''} — {h.get('snippet') or ''}"),
                      "reason": "social-media login wall, and the search for a readable copy was unavailable"})
            return False
        s = src("/web/search/live", body)
        res = [r for r in ((rec["data"] or {}).get("results") or [])
               if r.get("url") and not checks.is_social(r["url"], r.get("title"))]
        res.sort(key=lambda r: (int(r.get("position") or 99), r["url"]))
        ev(out, s, f"count(non-social results) for \"{ph}\" (copy of {h['url']})", len(res), rec["retrieved_at"])
        cand = res[:3]
        pages = _read_pages([r["url"] for r in cand], job, out)
        for r in cand:
            it, fat = pages.get(r["url"], (None, None))
            if not (it and it.get("success")):
                continue
            text, _ = checks.html_to_text(it.get("content") or "")
            full = (it.get("title") or "") + "\n" + text
            if checks.is_interstitial(it.get("title"), text) or not checks.name_positions(full, name):
                continue
            verdict, reason, psg = checks.classify_hit(full, person, company_names)
            h.update({"verdict": verdict, "basis": "readable copy", "passage": psg, "read_at": fat,
                      "reason": f"login wall; judged from a readable copy of the story at {r['url']}: {reason}",
                      "evidence_source": f"{r['url']} (readable copy of {h['url']}, via crustdata /web/enrich/live)"})
            return True

    snip = checks.norm_space(f"{h['title'] or ''} — {h.get('snippet') or ''}")
    verdict, reason, psg = checks.classify_hit(f"{h['title'] or ''}\n{h.get('snippet') or ''}", person,
                                               company_names)
    if verdict == "namesake_cleared":
        verdict = "namesake_unconfirmed"
        flag(out, "namesake_unconfirmed", "note",
             f"{name}: login-wall page with no readable copy (searched \"{phrase}\"); not counted against the "
             f"vendor because the snippet names no company, role or city. URL: {h['url']} Snippet: \"{snip}\"")
    h.update({"verdict": verdict, "basis": "snippet (login wall, no readable copy)", "passage": snip,
              "reason": f"login wall, no readable copy found (searched \"{phrase}\"); judged from the snippet: {reason}"})
    return True


def _adverse_hits_crustdata(job, name, out):
    q = f"\"{name}\" fraud OR arrested OR scam OR lawsuit OR \"shell company\""
    body = {"query": q, "sources": ["web", "news"]}
    rec = api("/web/search/live", body, job)
    if rec.get("data") is None:
        mark_unavailable(out, f"/web/search/live for {name} (GDELT fallback)", rec)
        return None, None
    s = src("/web/search/live", body)
    res = [r for r in ((rec["data"] or {}).get("results") or []) if r.get("url")]
    res.sort(key=lambda r: (int(r.get("position") or 99), r["url"]))
    ev(out, s, "count(results)", len(res), rec["retrieved_at"])
    return [{"source": "crustdata_web_search", "url": r["url"], "title": r.get("title"),
             "snippet": r.get("snippet"), "record_date": None, "found_via": s,
             "found_at": rec["retrieved_at"]} for r in res], "Crustdata web search (GDELT unavailable)"


def _read_pages(urls, job, out):
    """Fetch pages; google.com/goto redirects and failed direct pages go through human_mode."""
    pages = {}
    redirect = [u for u in urls if "google.com/goto" in u]
    direct = [u for u in urls if u not in redirect]
    jobs = [(batch[i:i + 10], human) for batch, human in ((direct, False), (redirect, True))
            for i in range(0, len(batch), 10)]
    # Direct and redirect batches are independent calls: fetch them at the same time.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(lambda j: (j[0], _fetch(j[0], job, human=j[1])), jobs))
    for chunk, (frec, _, items) in results:
        if frec.get("data") is None:
            mark_unavailable(out, f"/web/enrich/live for {len(chunk)} adverse-media page(s)", frec)
        for u, it in zip(chunk, items):
            pages[u] = (it, frec.get("retrieved_at"))

    def unread(u):
        it = (pages.get(u) or (None,))[0] or {}
        return not it.get("success") or checks.is_interstitial(
            it.get("title"), checks.html_to_text(it.get("content") or "")[0])
    retry = [u for u in direct if unread(u)]
    for i in range(0, len(retry), 10):
        chunk = retry[i:i + 10]
        frec, _, items = _fetch(chunk, job, human=True)
        for u, it in zip(chunk, items):
            if it.get("success"):
                pages[u] = (it, frec.get("retrieved_at"))
    return pages


# ---------- step: operating signals (fronts carry genuine papers) ----------
EMPLOYEE_SIGNAL_MIN = 3
JOB_LOOKBACK_DAYS = 180


def step_operating_signals(job, company_id, prof, web):
    out = new_out()
    d = out["data"]
    if not company_id:
        check(out, "operating_signals", "not_checked", "no company identified")
        return out

    # 1. current employees (Person Search, count only: limit 1, id field only)
    pb = {"filters": {"field": "experience.employment_details.current.company_id", "type": "=", "value": company_id},
          "fields": ["crustdata_person_id"], "limit": 1}
    pr = api("/person/search", pb, job)
    if pr.get("data") is None:
        mark_unavailable(out, "/person/search (employee count)", pr)
        d["current_employees"] = None
    else:
        d["current_employees"] = (pr["data"] or {}).get("total_count")
        ev(out, src("/person/search", pb), "total_count", d["current_employees"], pr["retrieved_at"])

    # 2. recent job postings (Job Search, metadata.date_added = date the listing was posted)
    since = (datetime.now(timezone.utc).date().toordinal() - JOB_LOOKBACK_DAYS)
    since = datetime.fromordinal(since).date().isoformat()
    jb = {"filters": {"op": "and", "conditions": [
              {"field": "company.basic_info.company_id", "type": "=", "value": company_id},
              {"field": "metadata.date_added", "type": "=>", "value": since}]},
          "fields": ["job_details.title", "job_details.url", "metadata.date_added"],
          "sorts": [{"field": "metadata.date_added", "order": "desc"}], "limit": 5}
    jr = api("/job/search", jb, job)
    if jr.get("data") is None:
        mark_unavailable(out, "/job/search", jr)
        d["recent_job_postings"] = None
    else:
        jobs = (jr["data"] or {}).get("job_listings") or []
        total = (jr["data"] or {}).get("total_count")
        d["recent_job_postings"] = total if total is not None else len(jobs)
        d["job_lookback_since"] = since
        js = src("/job/search", jb)
        ev(out, js, "total_count", d["recent_job_postings"], jr["retrieved_at"])
        for i, j in enumerate(jobs[:3]):
            ev(out, js, f"job_listings[{i}].job_details.title", dig(j, "job_details", "title"), jr["retrieved_at"],
               dig(j, "metadata", "date_added"))

    # 3. real operations described on the website (from website_check, no new call)
    wc = {c["check"]: c["status"] for c in web.get("checks", [])}
    wflags = {f["flag"] for f in web.get("red_flags", [])}
    if web.get("unavailable"):
        d["site_operations"] = None
    else:
        d["site_operations"] = (wc.get("website_describes_business") == "verified"
                                and wc.get("website_contact") == "verified"
                                and not wflags & {"parked_domain", "default_server_page", "template_text",
                                                  "thin_or_empty_site"})

    sig = {"employees": None if d["current_employees"] is None else d["current_employees"] >= EMPLOYEE_SIGNAL_MIN,
           "recent_jobs": None if d["recent_job_postings"] is None else d["recent_job_postings"] > 0,
           "site_operations": d["site_operations"]}
    d["signals"] = sig
    present = sum(1 for v in sig.values() if v)
    unknown = sum(1 for v in sig.values() if v is None)
    d["signals_present"] = present
    d["verified"] = present >= 2
    detail = (f"{d['current_employees']} current employees on record, "
              f"{d['recent_job_postings']} job postings since {d.get('job_lookback_since', '?')}, "
              f"website operations {'shown' if sig['site_operations'] else 'not shown' if sig['site_operations'] is False else 'unknown'}")
    if present >= 2:
        check(out, "operating_signals", "verified", detail)
    elif present == 0 and unknown == 0:
        check(out, "operating_signals", "failed", detail)
        if prof.get("status") == "active":
            flag(out, "possible_front", "warning",
                 "active on paper but no operating signals: " + detail
                 + " (LOOKS_OFF only with another concrete negative)")
    else:
        check(out, "operating_signals", "unclear", detail + (f"; {unknown} signal(s) unavailable" if unknown else ""))
    return out


# ---------- step: entity resolution (records share no common key) ----------
ER_WEIGHTS = {"domain": 0.30, "name_similarity": 0.20, "director_employer": 0.25,
              "website_names_company": 0.15, "city": 0.10}


def step_entity_resolution(outs, name, domain):
    out = new_out()
    ident, prof = outs["identify"]["data"], outs["company_profile"]["data"]
    ppl, web = outs["who_runs_it"]["data"], outs["website_check"]
    dc = ident.get("domain_company")
    signals = []

    def sig(s, result, detail):
        assert result in ("agree", "weak", "disagree", "unknown")
        signals.append({"signal": s, "result": result, "detail": detail, "weight": ER_WEIGHTS[s]})

    # domain <-> Crustdata company
    if dc:
        sig("domain", "agree" if dc["exact_domain"] else "weak",
            f"{domain} {'is listed on' if dc['exact_domain'] else 'fuzzy-matched'} \"{dc['name']}\" (id {dc['id']})")
    else:
        sig("domain", "unknown", f"no Crustdata company resolved from {domain}")

    # name <-> Crustdata company (rapidfuzz token_set_ratio after stripping legal suffixes)
    if ident.get("name_company") and dc:
        nc = ident["name_company"]
        sig("name_similarity", "disagree",
            f"\"{name}\" resolves to \"{nc['name']}\" (id {nc['id']}), not the website's company \"{dc['name']}\"")
    elif dc:
        s = dc["name_similarity"]
        res = "agree" if s >= 85 else "weak" if s >= 60 or dc.get("in_name_search") else "disagree"
        sig("name_similarity", res, f"rapidfuzz({name!r}, {dc['name']!r}) = {s}"
            + ("; the name search also returned this company" if dc.get("in_name_search") else ""))
    else:
        sig("name_similarity", "unknown", "no company to compare the name against")

    # directors' current employer
    people = ppl.get("people") or []
    known = [p for p in people if p.get("employer_match") is not None]
    if any(p.get("employer_match") for p in known):
        sig("director_employer", "agree",
            "; ".join(f"{p['name']}: {'current' if p['employer_match'] else 'NOT current'}" for p in known))
    elif known:
        sig("director_employer", "disagree",
            "; ".join(f"{p['name']}'s current employer is not this company" for p in known))
    else:
        sig("director_employer", "unknown", "no leader profile could be checked")

    # website text names the company
    wc = {c["check"]: c for c in web.get("checks", [])}
    wn = wc.get("website_names_company")
    if wn and wn["status"] == "verified":
        sig("website_names_company", "agree", wn["detail"])
    elif wn and wn["status"] == "failed" and web["data"].get("text_chars", 0) >= 300:
        sig("website_names_company", "disagree", wn["detail"])
    else:
        sig("website_names_company", "unknown", "site text not readable enough to say")

    # city: HQ city on the profile vs the address on the site / the leaders' cities
    hq_city = (prof.get("hq") or "").split(",")[0].strip()
    lead_cities = [p.get("city") for p in people if p.get("city")]
    city_hits = [c for c in lead_cities if hq_city and checks.core_city(c) and
                 checks.core_city(c) == checks.core_city(hq_city)]
    if web["data"].get("address_matches_hq"):
        sig("city", "agree", f"the website lists the profile's HQ ({prof.get('hq')})")
    elif city_hits:
        sig("city", "agree", f"leader city {city_hits[0]} matches HQ city {hq_city}")
    elif hq_city and lead_cities:
        # Leaders often live elsewhere, so a different city is recorded but not counted as a conflict.
        sig("city", "weak", f"HQ city {hq_city}; leader cities {lead_cities}")
    else:
        sig("city", "unknown", "no HQ city or no city to compare")

    agree = [s for s in signals if s["result"] == "agree"]
    disagree = [s for s in signals if s["result"] == "disagree"]
    conf = sum(s["weight"] for s in agree) + 0.5 * sum(s["weight"] for s in signals if s["result"] == "weak")
    if disagree and agree:
        status = "conflict"
        flag(out, "entity_signals_conflict", "warning",
             "signals disagree, so no company is picked: " + "; ".join(f"{s['signal']}: {s['detail']}" for s in disagree))
    elif not agree:
        status = "unlinked"
    elif conf >= 0.70:
        status = "linked"
    else:
        status = "weak"
    out["data"] = {"status": status, "match_confidence": round(conf, 2), "signals": signals,
                   "company_id": ident.get("company_id") if status in ("linked", "weak") else None}
    check(out, "entity_resolution", "verified" if status == "linked" else "failed" if status == "conflict" else "unclear",
          f"{status}, match_confidence {round(conf, 2)} from {len(agree)} agreeing signal(s)"
          + (f", {len(disagree)} disagreeing" if disagree else ""))
    return out


# ---------- step: decide ----------
def step_decide(outs):
    ident, prof, ppl, web, ops, er, adv, cadv = (outs[k] for k in (
        "identify", "company_profile", "who_runs_it", "website_check", "operating_signals",
        "entity_resolution", "adverse_media", "company_adverse_media"))
    flags = [f for o in outs.values() for f in o["red_flags"]]
    concrete = [f for f in flags if f["severity"] == "concrete"]
    unavailable = [u for o in outs.values() for u in o.get("unavailable", [])]
    ok = {
        "identify": any(c["check"] == "identify" and c["status"] == "verified" for c in ident["checks"]),
        "entity_resolution": er["data"].get("status") == "linked",
        "company_profile": bool(prof["data"].get("verified")),
        "people": bool(ppl["data"].get("verified")),
        "website": bool(web["data"].get("verified")),
        "operating_signals": bool(ops["data"].get("verified")),
        "adverse_media_clear": bool(adv["data"].get("complete")) and adv["data"].get("leaders_screened", 0) >= 1
                               and not any(h["verdict"] in ("adverse", "possible_adverse", "unreviewed")
                                           for h in adv["data"].get("hits", [])),
        "company_adverse_clear": bool(cadv["data"].get("complete"))
                                 and not any(h["verdict"] in ("adverse", "possible_adverse", "unreviewed")
                                             for h in cadv["data"].get("hits", [])),
    }
    why = {
        "identify": "could not tie the website to the named company",
        "entity_resolution": f"entity link is {er['data'].get('status')} (match_confidence {er['data'].get('match_confidence')})",
        "company_profile": "no active Crustdata profile with a founding year",
        "people": "no founder/CEO/director confirmed as currently at the company",
        "website": "website does not clearly name the company, list contact details and describe the business",
        "operating_signals": "fewer than 2 operating signals (employees, recent job postings, operations on the site); "
                             "active status alone does not prove trading",
        "adverse_media_clear": "adverse media not cleared (a hit that passed triage needs human review or "
                               "couldn't be read, or no leader could be screened)",
        "company_adverse_clear": "company-level adverse media not cleared (an article accuses the company, "
                                 "or a hit that passed triage couldn't be read)",
    }
    missing = [f"source unavailable: {u['source']} ({u['reason']})" for u in unavailable]
    missing += [why[k] for k, v in ok.items() if not v]
    # Unread adverse-media pages can only add negative evidence; they can't clear a vendor. So they
    # never stop a LOOKS_OFF that a page we DID read already confirms. Any other unavailable source
    # (identity, profile, website, DNS, search) could exonerate, so it still forces CANNOT_VERIFY.
    blocking = [u for u in unavailable if u.get("kind") != "adverse_page"]
    confirmed = [f for f in concrete if f["flag"] in ("company_adverse_media", "director_adverse_media",
                                                       "lookalike_domain", "parked_domain",
                                                       "default_server_page")]
    # Order matters, most cautious first:
    if blocking or (unavailable and not confirmed):
        verdict, basis = "CANNOT_VERIFY", "a source was unavailable; unavailable is never treated as negative"
    elif er["data"].get("status") == "conflict":
        verdict, basis = "CANNOT_VERIFY", "entity signals conflict, so no company is picked"
    elif concrete:
        verdict, basis = "LOOKS_OFF", "concrete negative evidence: " + "; ".join(f["flag"] for f in concrete)
        missing = []
    elif all(ok.values()):
        verdict, basis, missing = "REAL_AND_TRADING", "every check verified", []
    else:
        verdict, basis = "CANNOT_VERIFY", "not every check could be verified"
    return {"data": {"verdict": verdict, "basis": basis, "criteria": ok, "missing": missing,
                     "concrete_red_flags": concrete},
            "checks": [], "red_flags": [], "evidence": [], "unavailable": []}


# ---------- output ----------
def build_summary(outs, verdict, missing):
    prof = outs.get("company_profile", {}).get("data", {})
    people = outs.get("who_runs_it", {}).get("data", {}).get("people", [])
    flags = [f for o in outs.values() for f in o.get("red_flags", [])]
    who = "; ".join(f"{p['name']} ({p.get('title')}{', current role confirmed' if p.get('employer_match') else ', current role NOT confirmed'})"
                    for p in people) or "not established"
    age = (f"{prof['age_years']} years (founded {prof['year_founded']})" if prof.get("age_years") is not None
           else "unknown")
    off = "; ".join(f"[{f['severity']}] {f['detail']}" for f in flags) or "nothing found"
    er = outs.get("entity_resolution", {}).get("data") or {}
    ops = outs.get("operating_signals", {}).get("data") or {}
    return {"who_runs_it": who, "age": age, "company": prof.get("name"), "status": prof.get("status"),
            "hq": prof.get("hq"), "employee_count_range": prof.get("employee_count_range"),
            "entity_link": f"{er.get('status')} (match_confidence {er.get('match_confidence')})" if er else None,
            "operating_signals": (f"{ops.get('signals_present')}/3 — {ops.get('current_employees')} current employees, "
                                  f"{ops.get('recent_job_postings')} recent job postings, site operations "
                                  f"{ops.get('site_operations')}") if ops.get("signals") else None,
            "anything_off": off}


def utf8_console():
    """Windows consoles and redirected output default to a legacy code page that can't print
    names like 'Karnātaka'; never let printing the result crash the run."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--website", required=True)
    ap.add_argument("--recheck", action="store_true",
                    help="re-run every step from cached API responses (no re-spend); approvals are kept")
    a = ap.parse_args()
    t0 = time.time()
    name, website = checks.norm_space(a.name), checks.normalize_url(a.website)
    domain = checks.domain_of(website)
    job = job_id_for(name, website)
    store.create_job(job, {"name": name, "website": website})
    if a.recheck:
        store.reset_job_steps(job)
    limit = cc.CFG.get("time_limit_seconds_per_run", 180)
    cc.DEADLINE = t0 + limit - 15  # leaves room to decide and write the output
    print(f"job {job}  ({name} / {domain})")

    outs, stopped = {}, None

    def run(step, fn):
        if time.time() - t0 > limit:
            raise StopRun(f"time limit of {limit}s reached before step {step}")
        s0 = time.time()
        outs[step] = store.run_step(job, step, fn)
        step_seconds[step] = round(time.time() - s0, 1)
        if outs[step].get("unavailable") or (step in ("decide", "approval") and any(
                o.get("unavailable") for o in outs.values())):
            store.reset_step(job, step)  # re-run it next time; cached calls inside it cost nothing
        return outs[step]

    step_seconds = {}

    def in_order():
        """Parallel steps finish in any order; keep outputs (and so evidence/checks) in pipeline order."""
        items = sorted(outs.items(), key=lambda kv: STEP_ORDER.index(kv[0]) if kv[0] in STEP_ORDER else 99)
        outs.clear()
        outs.update(items)

    def run_parallel(*steps):
        """Independent steps at the same time; each is still saved on its own when it finishes."""
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(run, s, f) for s, f in steps]
            return [f.result() for f in futures]

    try:
        ident = run("identify", lambda: step_identify(job, name, domain))
        cid = ident["data"]["company_id"]
        prof = run("company_profile", lambda: step_company_profile(job, cid, domain))
        ppl, web = run_parallel(
            ("who_runs_it", lambda: step_who_runs_it(job, cid)),
            ("website_check", lambda: step_website_check(job, website, name, prof["data"])))
        cnames = sorted(n for n in {name, prof["data"].get("name")} if n)
        run_parallel(
            ("operating_signals", lambda: step_operating_signals(job, cid, prof["data"], web)),
            ("adverse_media", lambda: step_adverse_media(job, ppl["data"]["people"], cnames, domain)),
            ("company_adverse_media", lambda: step_company_adverse_media(job, cnames, domain)))
        run("entity_resolution", lambda: step_entity_resolution(outs, name, domain))
        in_order()
        dec = run("decide", lambda: step_decide(outs))
        verdict = dec["data"]["verdict"]

        def approval():
            # Never marks the vendor cleared: it only files a pending request for a human.
            requested = verdict == "REAL_AND_TRADING"
            if requested:
                store.request_approval(job, ACTION)
            else:
                store.withdraw_approval(job, ACTION)
            return {"data": {"requested": requested}, "checks": [], "red_flags": [], "evidence": []}
        run("approval", approval)
    except (StopRun, cc.BudgetExceeded) as e:
        stopped = str(e)
    except KeyboardInterrupt:
        # Nothing to clean up: a step is saved only after it finishes, cache files are written
        # atomically, and an approval request is filed only in the last step.
        print(f"\ninterrupted; finished steps are saved ({', '.join(outs) or 'none'}). "
              "Run the same command again to resume.")
        return 130

    in_order()
    dec = outs.get("decide", {}).get("data")
    unavailable = [u for o in outs.values() for u in o.get("unavailable", [])]
    if dec:
        verdict, missing = dec["verdict"], dec["missing"]
    else:
        verdict = "CANNOT_VERIFY"
        missing = [f"run stopped early: {stopped}; re-run the same command to resume"]
        missing += [f"source unavailable: {u['source']} ({u['reason']})" for u in unavailable]

    evidence = [e for o in outs.values() for e in o.get("evidence", [])]
    store.clear_evidence(job)
    for e in evidence:
        store.add_evidence(job, e["field_or_passage"], e["source"], e["field_or_passage"], e["value"],
                           e["record_date"], e["retrieved_at"])

    result = {
        "job_id": job,
        "input": {"name": name, "website": website},
        "verdict": verdict,
        "verdict_basis": (dec or {}).get("basis", "run stopped early"),
        "summary": build_summary(outs, verdict, missing),
        "missing": missing,
        "entity_resolution": outs.get("entity_resolution", {}).get("data"),
        "operating_signals": outs.get("operating_signals", {}).get("data"),
        "checks": [c for o in outs.values() for c in o.get("checks", [])],
        "red_flags": [f for o in outs.values() for f in o.get("red_flags", [])],
        "adverse_media": outs.get("adverse_media", {}).get("data", {}).get("hits", []),
        "company_adverse_media": outs.get("company_adverse_media", {}).get("data", {}).get("hits", []),
        "unavailable_sources": unavailable,
        # Every source that was slow or down: GDELT fallbacks, plus each unavailable source
        # (Crustdata timeouts/outages and unreadable pages), so "down" is never mistaken for "clean".
        "sources_degraded": outs.get("adverse_media", {}).get("data", {}).get("sources_degraded", [])
                            + outs.get("company_adverse_media", {}).get("data", {}).get("sources_degraded", [])
                            + [{"source": u["source"], "status": "source unavailable", "reason": u["reason"]}
                               for u in unavailable],
        "retries": store.get_retries(job),
        "evidence": evidence,
        "approval": {"action": ACTION, "status": store.approval_status(job, ACTION),
                     "how": f"python approve.py {job}" if verdict == "REAL_AND_TRADING" else None,
                     **({"warning": f"approved earlier, but the current verdict is {verdict}; review it"}
                        if store.approval_status(job, ACTION) == "approved" and verdict != "REAL_AND_TRADING"
                        else {})},
        "steps_completed": list(outs.keys()),
        "stopped": stopped,
        "cost_credits": round(store.job_cost(job), 2),
        "run_cost_credits": round(cc.RUN_SPENT.get(job, 0), 2),
        "runtime_seconds": round(time.time() - t0, 1),
        "step_seconds": {k: step_seconds[k] for k in STEP_ORDER if k in step_seconds},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    store.finish_job(job, {"verdict": verdict}, status="done" if not stopped else "stopped")
    out_dir = os.path.join(store.DATA_DIR, "output")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{job}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    store.save_result(job, result)  # the API reads results from the store (any server instance)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\nverdict: {verdict}   saved: {path}")
    return 0 if not stopped else 2


if __name__ == "__main__":
    sys.exit(main())
