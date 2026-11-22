"""Pure helpers for run.py: name/domain matching, HTML to text, site and adverse-media rules.

Nothing here calls an API or knows any company facts; every judgment is made on text
that came back from Crustdata or a fetched page.
"""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from rapidfuzz import fuzz

# ---------- names and domains ----------
LEGAL_SUFFIXES = {
    "pvt", "private", "ltd", "limited", "llp", "llc", "inc", "incorporated", "corp",
    "corporation", "co", "company", "plc", "gmbh", "pte", "sa", "ag", "bv", "the", "opc",
}


def norm_space(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def norm_name(s):
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(t for t in s.split() if t not in LEGAL_SUFFIXES)


# Descriptor words that don't identify a company: 'Shadowfax Technologies' is still 'Shadowfax'.
GENERIC_WORDS = {
    "technologies", "technology", "tech", "solutions", "services", "service", "systems", "software",
    "labs", "lab", "global", "international", "india", "group", "holdings", "ventures", "enterprises",
    "industries", "infotech", "networks", "digital", "and",
}
MIN_TOKEN_RATIO = 0.7


def core_tokens(name):
    toks = norm_name(name).split()
    return [t for t in toks if t not in GENERIC_WORDS] or toks


def name_similarity(a, b):
    """0-100. Legal suffixes and descriptor words are ignored ('Shadowfax Technologies Pvt Ltd' ==
    'Shadowfax'). token_set_ratio alone scores any subset as 100, so 'Grain' vs 'Grain Garden
    Delight' would look identical: when the shorter name has under 70% of the longer one's
    distinctive tokens, the score falls back to plain whole-string similarity."""
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0
    tsr = fuzz.token_set_ratio(na, nb)
    ca, cb = core_tokens(a), core_tokens(b)
    short, long_ = sorted((len(set(ca)), len(set(cb))))
    if long_ and short / long_ < MIN_TOKEN_RATIO:
        return min(tsr, fuzz.ratio(" ".join(ca), " ".join(cb)))
    return tsr


def normalize_url(website):
    w = (website or "").strip()
    if not re.match(r"^https?://", w, re.I):
        w = "https://" + w
    return w


def domain_of(website):
    host = urlparse(normalize_url(website)).netloc.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def domain_similarity(a, b):
    return fuzz.ratio(a or "", b or "")


def classify_doh(answer):
    """A DNS-over-HTTPS JSON answer -> 'exists' | 'nxdomain' | 'error'.
    RCODE 0 = the name exists (even with no A record), 3 = NXDOMAIN (definitively no such domain),
    anything else (SERVFAIL, REFUSED, ...) says nothing about the domain."""
    status = (answer or {}).get("Status")
    return {0: "exists", 3: "nxdomain"}.get(status, "error")


_SECOND_LEVEL = {"co", "com", "net", "org", "gov", "ac", "edu", "firm", "gen", "ind"}
_CONFUSABLE = str.maketrans({"0": "o", "1": "l", "i": "l", "|": "l", "5": "s", "3": "e"})


def domain_label(domain):
    """'shadowfax.in' -> 'shadowfax', 'acme.co.in' -> 'acme' (the part a typosquatter imitates)."""
    parts = (domain or "").lower().split(".")
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL:
        parts = parts[:-2]
    elif len(parts) >= 2:
        parts = parts[:-1]
    return parts[-1] if parts else ""


def is_lookalike(domain, real_domains, min_sim=80):
    """A near-copy of a real company's domain: the name part is a typo or a confusable-character
    swap of the real one ('retooi.com' vs 'retool.com', 'shadowfaax.in' vs 'shadowfax.in').
    The same name on another TLD ('shadowfax.co') is NOT a lookalike: companies own several.
    Returns (is_lookalike, closest_real_domain, similarity)."""
    d, lab = (domain or "").lower(), domain_label(domain)
    best = (False, None, 0)
    for real in real_domains or []:
        r = (real or "").lower()
        if not r:
            continue
        if r == d:
            return False, r, 100
        rl = domain_label(r)
        if not lab or not rl or lab == rl:
            continue
        sim = fuzz.ratio(lab, rl)
        if lab.translate(_CONFUSABLE) == rl.translate(_CONFUSABLE):
            sim = max(sim, 99)
        if sim >= min_sim and sim > best[2]:
            best = (True, r, sim)
    return best


# ---------- HTML ----------
class _TextLinks(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self._skip, self._href, self._atext = [], [], 0, None, []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "a":
            self._href, self._atext = dict(attrs).get("href"), []
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section", "footer", "address"):
            self.parts.append("\n")
        else:
            self.parts.append(" ")  # keep adjacent elements apart: "112w" + "Praharsh" must not fuse

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        self.parts.append(" ")
        if tag == "a" and self._href:
            self.links.append((self._href, norm_space(" ".join(self._atext))))
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._atext.append(data)


def html_to_text(html):
    p = _TextLinks()
    try:
        p.feed(html or "")
    except Exception:
        pass
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text, p.links


LINK_KINDS = [  # (kind, path-segment pattern, exact link labels) in priority order
    ("contact", re.compile(r"(^|[/_-])contact(-?us)?($|[/_.-])"), {"contact", "contact us", "get in touch"}),
    ("about", re.compile(r"(^|[/_-])(about(-?us)?|company|who-we-are)($|[/_.-])"), {"about", "about us", "company"}),
    ("privacy", re.compile(r"(^|[/_-])privacy(-?policy|-?notice)?($|[/_.-])"), {"privacy", "privacy policy"}),
    ("legal", re.compile(r"(^|[/_-])(legal|terms|imprint|impressum)($|[/_.-])"),
     {"legal", "terms", "terms of use", "terms of service", "imprint"}),
]


def same_site_links(base_url, links, max_pages=3):
    """Same-domain contact / about / privacy-legal pages, matched on the URL path or an exact
    link label, so 'Learn more about apps' doesn't count. Legal pages often carry the address."""
    site = domain_of(base_url)
    found = {}
    for href, label in links:
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        full = urljoin(base_url, href).split("#")[0].split("?")[0]
        host = domain_of(full)
        if not (host == site or host.endswith("." + site)) or full.rstrip("/") == base_url.rstrip("/"):
            continue  # same site, subdomains included (legal pages often live on docs./legal.)
        path, lab = urlparse(full).path.lower(), label.lower().strip()
        for kind, pat, labels in LINK_KINDS:
            if kind not in found and (pat.search(path) or lab in labels):
                found[kind] = full
                break
    return [found[k] for k, _, _ in LINK_KINDS if k in found][:max_pages]


INTERSTITIAL = ["verifying your request", "checking your browser", "just a moment", "attention required",
                "enable javascript and cookies to continue", "access denied", "are you a robot",
                "unusual traffic from your computer", "the request could not be satisfied",
                "request blocked", "error 403", "403 forbidden"]


def is_interstitial(title, text):
    """A bot-check / redirect page instead of the real one. Its content says nothing about the target."""
    t, low = (title or "").strip().lower(), (text or "")[:1500].lower()
    if t in ("google", "just a moment...", "attention required! | cloudflare", "access denied", "403 forbidden"):
        return True
    if t.startswith("error: the request could not be satisfied"):  # CloudFront block page
        return True
    return len(text or "") < 1500 and any(p in low for p in INTERSTITIAL)


def is_abbreviated(name):
    """'Anthony G.' style names (surname hidden on the profile) can't be searched for adverse media."""
    toks = norm_space(name).split()
    return len(toks) < 2 or len(toks[-1].rstrip(".")) <= 1


def core_city(city):
    c = re.sub(r"\b(greater|metropolitan|metro|bay area|area|region|district|urban|city)\b", " ", (city or "").lower())
    return norm_space(c.split(",")[0])


def passage(text, start, end=None, radius=160):
    end = start if end is None else end
    a, b = max(0, start - radius), min(len(text), end + radius)
    return norm_space(("..." if a else "") + text[a:b] + ("..." if b < len(text) else ""))


# ---------- website rules ----------
PARKED = [
    "this domain is for sale", "domain is for sale", "buy this domain", "this domain may be for sale",
    "domain name is for sale", "hugedomains", "sedo domain parking", "parked free", "parkingcrew",
    "this domain has been registered", "domain parking", "is parked",
]
DEFAULT_PAGES = [
    "welcome to nginx", "apache2 ubuntu default page", "it works!", "default web site page",
    "iis windows server", "index of /", "cpanel", "future home of something quite cool",
]
TEMPLATE = ["lorem ipsum", "just another wordpress site", "sample page", "hello world!",
            "coming soon", "under construction", "website is under construction"]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,5}\)?[\s.-]?){2,4}\d{3,4}")
PIN_RE = re.compile(r"\b(?:[1-9]\d{5}|[1-9]\d{2}\s\d{3}|\d{5}(?:-\d{4})?)\b")
ADDRESS_WORDS = re.compile(r"\b(address|office|floor|road|street|st\.|nagar|sector|plot|building|"
                           r"tower|marg|lane|avenue|suite|block|pin|pincode|zip)\b", re.I)


def find_first(patterns, low):
    for p in patterns:
        i = low.find(p)
        if i >= 0:
            return p, i
    return None, -1


def find_phone(text):
    for m in PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 10 <= len(digits) <= 13 and not re.fullmatch(r"(19|20)\d{2}(19|20)\d{2}.*", digits):
            return m
    return None


def find_address(text, hq_terms):
    """An HQ city/street from the Crustdata profile on the page, or a postcode next to address words."""
    low = text.lower()
    for term in hq_terms:
        t = (term or "").strip().lower()
        if len(t) >= 4:
            i = low.find(t)
            if i >= 0:
                return term, i
    for m in PIN_RE.finditer(text):
        window = text[max(0, m.start() - 120): m.end() + 40]
        if ADDRESS_WORDS.search(window):
            return m.group(), m.start()
    return None, -1


STOP = set("""about above after again against all also among and another any are because been before
being below between both but can could did does doing down during each few from further had has have
having here how into its itself just more most must not now off once only other our out over own same
should some such than that the their them then there these they this those through too under until very
was were what when where which while who whom why will with would you your yours we us via per new get
make makes made one two use used using across within every company companies business businesses
services service solutions solution team teams people world leading based provider providers best""".split())


def keywords(text):
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in STOP}


# ---------- adverse media rules ----------
ADVERSE_TERMS = [r"fraud\w*", r"arrest\w*", r"scam\w*", r"lawsuits?", r"shell compan(?:y|ies)", r"cheat\w*",
                 r"embezzl\w*", r"money[- ]launder\w*", r"sued", r"charged with", r"convicted", r"indicted",
                 r"fir (?:against|registered)", r"ponzi", r"abscond\w*", r"forger\w*", r"sanctioned"]
# Words that name a dispute, not wrongdoing: on their own they never make LOOKS_OFF.
DISPUTE_ONLY = re.compile(r"^(lawsuits?|sued|fir (?:against|registered))$")
ROLE_TERMS = ["co-founder", "cofounder", "founder", "chief executive", "ceo", "managing director",
              "director", "chairman", "chairperson", "owner", "proprietor", "partner", "cto", "coo", "cfo"]


def name_positions(text, full_name):
    """Where the person's full name appears (also 'First Last' when a middle name is on the profile)."""
    low = text.lower()
    variants = {norm_space(full_name).lower()}
    toks = [t for t in re.split(r"\s+", norm_space(full_name)) if t and not t.endswith(".")]
    if len(toks) > 2:
        variants.add(f"{toks[0]} {toks[-1]}".lower())
    hits = []
    for v in variants:
        if len(v) < 5:
            continue
        hits += [(m.start(), m.end(), v) for m in re.finditer(r"\b" + re.escape(v) + r"\b", low)]
    return sorted(hits)


def triage(text, full_name, company_names):
    """Before any fetch: a hit only needs reading if its title/snippet has the person's full name
    AND (an adverse keyword OR the company name). Returns (needs_read, reason)."""
    has_name = bool(name_positions(text, full_name))
    low = (text or "").lower()
    adverse = [m.group() for t in ADVERSE_TERMS for m in [re.search(r"\b" + t + r"\b", low)] if m]
    flat = " " + " ".join(norm_name(text).split()) + " "
    company = next((c for c in company_names if norm_name(c) and f" {norm_name(c)} " in flat), None)
    if not has_name:
        return False, f"title/snippet does not contain the full name \"{full_name}\""
    if not adverse and not company:
        return False, f"has \"{full_name}\" but no adverse keyword and no company name in the title/snippet"
    return True, ("title/snippet has the full name and "
                  + " and ".join(x for x in [f"adverse term(s) {adverse}" if adverse else "",
                                             f"company \"{company}\"" if company else ""] if x))


def role_terms_for(title):
    t = (title or "").lower()
    return [r for r in ROLE_TERMS if re.search(r"\b" + re.escape(r) + r"\b", t)]


MENTIONED_CUES = re.compile(
    r"\b(plaintiffs?|petitioners?|applicants?|victims?|witness(?:es)?|complainants?|whistle-?blowers?|"
    r"informants?|beneficiar(?:y|ies)|on behalf of|filed by|denied (?:an? )?(?:h-?1b |work |employment )?visa to|"
    r"visa (?:application|petition) (?:for|of)|duped|cheated of)\b")
ACCUSED_CUES = re.compile(
    r"\b(accused|arrested|charged|convicted|indicted|defendants?|absconding|booked|detained|"
    r"named in (?:the |an )?fir|chargesheet(?:ed)?|pleaded guilty|found guilty|sentenced|"
    r"(?:allegedly|who) (?:cheated|defrauded|duped|embezzled|siphoned))\b")


def party_role(low, s, e, window=300):
    """Which side of a case the text puts the person on, judged near the mention.
    Returns (mentioned_cue or None, accused_cue or None)."""
    win = low[max(0, s - window): e + window]
    m, a = MENTIONED_CUES.search(win), ACCUSED_CUES.search(win)
    label = None
    if m:
        cue = m.group()
        label = ("visa applicant" if "visa" in cue else "party the case was filed for" if cue in ("on behalf of", "filed by")
                 else "victim" if cue in ("duped", "cheated of") else re.sub(r"(es|s)$", "", cue))
    return (label, a.group() if a else None)


SOCIAL_DOMAINS = ("facebook.com", "instagram.com", "linkedin.com", "x.com", "twitter.com", "threads.net")
SOCIAL_TITLE = re.compile(r"(\|\s*facebook|^facebook$|instagram|\|\s*linkedin|^linkedin|\bon x\b|[|/]\s*x\s*$|"
                          r"^x$|twitter)", re.I)


def is_social(url, *titles):
    """A Facebook / Instagram / LinkedIn / X page (by domain, or by title when the link is a redirect)."""
    host = domain_of(url or "")
    if any(host == d or host.endswith("." + d) for d in SOCIAL_DOMAINS):
        return True
    return any(t and SOCIAL_TITLE.search(t.strip()) for t in titles)


def key_phrase(title, snippet, max_words=12):
    """The headline (or the snippet's first sentence when the headline is generic) to look for elsewhere."""
    t = re.sub(r"\s*(\.\.\.|…)\s*$", "", norm_space(title or ""))
    t = re.split(r"\s+[|–-]\s+(?=[^|–-]*$)", t)[0]            # drop a trailing " | Site name"
    if len(t.split()) < 4 or SOCIAL_TITLE.search(t):
        sent = re.split(r"(?<=[.!?])\s|\.\.\.|…", norm_space(snippet or ""))[0]
        sent = re.sub(r"^[A-Z][a-z]{2,8} \d{1,2},? \d{4}\s*[—-]\s*", "", sent)  # leading "Feb 13, 2017 — "
        t = sent if len(sent.split()) >= 4 else t
    return " ".join(t.split()[:max_words])


def copy_search_phrases(title, snippet, max_words=12):
    """Phrases to look for a readable copy of a login-walled story: the headline first, then the
    snippet's first sentence (a social post's headline and snippet are sometimes different stories)."""
    first = key_phrase(title, snippet, max_words)
    sent = re.split(r"(?<=[.!?])\s|\.\.\.|…", norm_space(snippet or ""))[0]
    sent = re.sub(r"^[A-Z][a-z]{2,8} \d{1,2},? \d{4}\s*[—-]\s*", "", sent)
    second = " ".join(sent.split()[:max_words])
    return [p for i, p in enumerate([first, second]) if len(p.split()) >= 4 and p not in [first, second][:i]]


def headline_core(title, min_words=5, max_words=20):
    """The searchable part of a result title: no trailing ' | Site' / ' - Site', no '...', no
    leading section tag ('Anti-EC protest | ...'). None when it's too short or generic to look up,
    or is itself a URL (Google shows 'http://site/path...' for pages it has no title for)."""
    t = norm_space(title or "")
    if not t or re.match(r"(https?://|www\.)", t, re.I):
        return None
    t = re.sub(r"\s*(\.\.\.|…)\s*$", "", t)
    parts = [p.strip() for p in re.split(r"\s+[|–—-]\s+", t) if p.strip()]
    t = max(parts, key=lambda p: len(p.split())) if parts else t  # the headline is the longest part
    t = norm_space(re.sub(r"[\"“”‘’'�]", "", t))
    words = t.split()
    if len(words) < min_words:
        return None
    return " ".join(words[:max_words])


def _headline_key(title):
    t = headline_core(title, min_words=1, max_words=40) or ""
    return " ".join(re.sub(r"[^\w\s]", " ", t.lower()).split())


def same_headline(a, b):
    """Is result title b the same story as title a? Google truncates titles, so the shorter one
    only has to match the start of the longer one."""
    ka, kb = _headline_key(a), _headline_key(b)
    if min(len(ka.split()), len(kb.split())) < 5:
        return False
    if fuzz.ratio(ka, kb) >= 90:          # the same title, give or take quotes and punctuation
        return True
    short, long_ = sorted((ka.split(), kb.split()), key=len)
    n = len(short) - 1                      # the last word of a truncated title may be cut off
    return n >= 8 and long_[:n] == short[:n]


def site_name(url):
    """'https://www.indianexpress.com/article/...' -> 'indianexpress.com'; Google links -> None."""
    d = domain_of(url or "")
    return None if not d or d.startswith("google.") else d


# ---------- company-level adverse media ----------
COMPANY_RISK_TERMS = ADVERSE_TERMS + [r"booked", r"raid\w*", r"fake (?:firm|company)", r"front company",
                                      r"dummy (?:firm|company|directors?)", r"hawala", r"forged"]
IMPERSONATION = re.compile(
    r"\b(pos(?:ing|ed|es)? as|impersonat\w*|in the name of|pretend\w* to be|"
    r"(?:using|misusing|misused|used) (?:its|their|the company'?s?|the) (?:name|brand|logo)|"
    r"fake (?:\w+ ){0,2}(?:agents?|executives?|employees?|staff|websites?|sites?|apps?|jobs?|offers?|"
    r"recruit\w*|calls?|messages?|delivery\w*)|phishing|lookalike|spoof\w*|cloned)\b")
ENTITY_CUES = re.compile(
    r"\b(shell (?:company|companies|firm|firms|entity|entities)|front (?:company|firm)|"
    r"dummy (?:company|firm|directors?)|fake (?:company|firm)|bogus (?:company|firm)|"
    r"(?:floated|set up|created|incorporated|used)\b(?:\W+\w+){0,8}?\W+(?:to|for)\W+(?:\w+\W+){0,4}?"
    r"(?:route|routing|launder\w*|siphon\w*|layer\w*|conduit)|banking conduit|money[- ]launder\w*|hawala|ponzi)\b")
LEADER_ARREST = re.compile(
    r"\b(directors?|promoters?|managing director|founders?|co-?founders?|owners?|proprietors?|ceo|chairman)\b"
    r"(?:\W+\w+){0,8}?\W+(arrested|apprehended|nabbed|booked|detained|charge-?sheeted)\b|"
    r"\b(arrested|apprehended|nabbed|booked|detained)\b(?:\W+\w+){0,8}?\W+"
    r"(directors?|promoters?|founders?|owners?|proprietors?)\b")


def _company_patterns(company_names):
    """One regex per distinct company name (legal suffixes dropped), matching across punctuation."""
    pats, seen = [], set()
    for n in company_names or []:
        toks = norm_name(n).split()
        key = " ".join(toks)
        if not toks or key in seen or len(key) < 3:
            continue
        seen.add(key)
        pats.append((n, re.compile(r"\b" + r"[\W_]+".join(re.escape(t) for t in toks) + r"\b", re.I)))
    return pats


def company_triage(text, company_names):
    """A company hit needs reading only if its title/snippet has the company's full name AND a risk
    keyword. Returns (needs_read, reason)."""
    low = (text or "").lower()
    named = next((n for n, p in _company_patterns(company_names) if p.search(text or "")), None)
    risk = [m.group() for t in COMPANY_RISK_TERMS for m in [re.search(r"\b" + t + r"\b", low)] if m]
    if not named:
        return False, f"title/snippet does not contain the company's full name ({', '.join(company_names)})"
    if not risk:
        return False, f"names \"{named}\" but has no risk keyword"
    return True, f"title/snippet names \"{named}\" with risk keyword(s) {risk}"


# A link to another story ("Related article: ...", "Read more: ...") just before the mention.
TEASER_BEFORE = re.compile(
    r"(related (?:article|story|stories|news|reading)|read more|also read|see also|recommended|"
    r"trending|more from|you may (?:also )?like|top stories|latest news|must read)\s*:?[^.\n]{0,160}$")
_SENT_END = re.compile(r"(?<=[.!?])\s+(?=[\"'“(]?[A-Z0-9])|\n")


def _sentence_window(text, s, e, cap):
    """Bounds of the sentence containing text[s:e], at most `cap` characters either side."""
    lo, hi = max(0, s - cap), min(len(text), e + cap)
    ends = list(_SENT_END.finditer(text, lo, s))
    a = ends[-1].end() if ends else lo
    nxt = _SENT_END.search(text, e, hi)
    b = nxt.start() if nxt else hi
    return a, b


def classify_company_hit(text, company_names, window=150):
    """Is the page accusing THE COMPANY? Only if accusing wording sits next to its name:
    shell/front/dummy firm, set up to route or launder money, its directors/promoters arrested,
    an FIR/case against it, or it being booked/raided. A mention where it is impersonated,
    the victim, or the complainant is not an accusation.
    Returns (verdict, reason, passage):
      adverse            accused of fraud / cheating / money laundering, a shell/fake/bogus company, the
                         company or its directors arrested or charged, or sanctioned  -> LOOKS_OFF
      possible_adverse   only a plain FIR, lawsuit, raid or commercial/payment dispute -> human review
      not_adverse        impersonated, victim or complainant
      no_adverse_content | not_about_company"""
    text = text or ""
    low = text.lower()
    mentions = []
    for n, p in _company_patterns(company_names):
        mentions += [(m.start(), m.end(), n, p) for m in p.finditer(text)]
    if not mentions:
        return "not_about_company", f"page does not name the company ({', '.join(company_names)})", None
    best = None
    teaser_only = True
    for s, e, n, p in sorted(mentions):
        # A mention inside a "related article" / "read more" teaser links to another story.
        if TEASER_BEFORE.search(low[max(0, s - 160):s]):
            continue
        teaser_only = False
        # Accusing wording must sit in the SAME sentence as the name, and close to it: a page
        # can name the company in one story and accuse someone else in the next.
        a, b = _sentence_window(text, s, e, window)
        win = low[a:b]
        co = p.pattern
        crime = r"(?:fraud\w*|cheat\w*|defraud\w*|money[- ]launder\w*|forger\w*|scam\w*|embezzl\w*)"
        # Serious: an accusation of fraud / a crime / a sham company / sanctions -> LOOKS_OFF.
        serious = (ENTITY_CUES.search(win) or LEADER_ARREST.search(win) or re.search(
            rf"{co}(?:\W+\w+){{0,4}}?\W+(?:was\s+|were\s+|has been\s+|is\s+)?(?:charged|charge-?sheeted|indicted|convicted)\b"
            rf"|{co}(?:\W+\w+){{0,6}}?\W+(?:accused of|allegedly|involved in|probed for|under investigation for|"
            rf"booked for|booked under|charged with)\W+(?:\w+\W+){{0,4}}?{crime}"
            rf"|(?:police|cops|ed|cbi|eow)\s+(?:have\s+)?booked\s+(?:the\s+)?(?:company\s+)?{co}(?:\W+\w+){{0,6}}?\W+(?:for|under)\W+(?:\w+\W+){{0,4}}?{crime}"
            rf"|{crime}\s+(?:\w+\s+){{0,2}}(?:by|at|involving)\s+(?:the\s+)?{co}"
            rf"|(?:charge-?sheet\w*|charges?)\s+(?:\w+\s+){{0,3}}against\s+(?:the\s+)?(?:company\s+)?{co}"
            rf"|sanctioned\s+(?:the\s+)?(?:company\s+)?{co}|sanctions?\s+(?:\w+\s+){{0,3}}(?:on|against)\s+(?:the\s+)?{co}"
            rf"|{co}(?:\W+\w+){{0,6}}?\W+(?:was\s+|has been\s+)?(?:sanctioned|blacklisted|debarred)\b", win, re.I))
        # Dispute: a plain FIR / lawsuit / commercial or payment dispute / raid -> possible_adverse only.
        dispute = re.search(
            rf"(?:fir|case|complaint|suit|lawsuit|petition)\s+(?:\w+\s+){{0,3}}against\s+(?:the\s+)?(?:company\s+)?{co}"
            rf"|{co}(?:\W+\w+){{0,3}}?\W+(?:was\s+|has been\s+)?(?:sued|raided)\b|sued\s+(?:the\s+)?{co}"
            rf"|{co}(?:\W+\w+){{0,3}}?\W+(?:was\s+|is\s+|has been\s+)?accused of\b"
            rf"|(?:police|cops)\s+(?:have\s+)?booked\s+(?:the\s+)?{co}|{co}(?:\W+\w+){{0,3}}?\W+booked\s+(?:under|by)\b"
            rf"|raid(?:s|ed)?\s+(?:on\s+|at\s+)?(?:the\s+)?(?:offices?\s+|premises\s+)?(?:of\s+)?{co}"
            rf"|{co}(?:\W+\w+){{0,8}}?\W+(?:payment dispute|commercial dispute|legal dispute|unpaid dues|arbitration|"
            rf"insolvency petition|defaulted on)", win, re.I)
        complainant = re.search(
            rf"{co}\s+(?:has\s+|had\s+)?(?:filed|lodged|registered)\s+(?:a\s+|an\s+)?(?:police\s+)?(?:fir|complaint|case)",
            win, re.I)
        imp = IMPERSONATION.search(win)
        if serious and not imp and not complainant:
            cand = (4, "adverse", f"\"{n}\" with accusing wording \"{checks_trim(serious.group())}\" next to it", s, e)
        elif (serious or dispute) and (imp or complainant):
            why = (f"impersonated (\"{imp.group()}\")" if imp else "the complainant")
            cand = (2, "not_adverse", f"\"{n}\" is {why} here, not accused", s, e)
        elif dispute:
            cand = (3, "possible_adverse",
                    f"\"{n}\" is in a dispute or case (\"{checks_trim(dispute.group())}\"), not accused of fraud, a "
                    "crime or being a sham; needs a human to read it", s, e)
        else:
            cand = (1, "no_adverse_content", f"names \"{n}\" but no accusing wording next to it", s, e)
        if best is None or cand[0] > best[0]:
            best = cand
    if best is None:
        why = "only in a 'related article' / 'read more' link, not in the story itself" if teaser_only else "nowhere"
        return "no_adverse_content", f"names the company {why}", None
    _, verdict, reason, s, e = best
    return verdict, reason, passage(text, s, e, radius=260)


def checks_trim(s, n=80):
    s = norm_space(s)
    return s if len(s) <= n else s[:n] + "..."


def classify_hit(text, person, company_names, window=600):
    """Spec rule: adverse only if the page mentions the person AND (the company OR their role/city).

    The company may appear anywhere on the page; role and city must sit near a mention of the
    person, and an adverse term must too, so sidebars and unrelated headlines don't count.
    Verdicts:
      adverse            person + company + adverse term near the name  (concrete)
      possible_adverse   person + role/city only + adverse term          (a same-city namesake fits this
                         too, so it blocks a clean verdict but is not proof on its own)
      no_adverse_content person + context, but no adverse term near the name
      mentioned_not_adverse  adverse term near the name, but the person is plaintiff / applicant /
                         victim / witness there and nothing near the name accuses them
      namesake_cleared   no mention, or a mention with none of company/role/city
    Returns (verdict, reason, passage_text).
    """
    name = person["name"]
    hits = name_positions(text, name)
    if not hits:
        return "namesake_cleared", f"text does not mention \"{name}\"", None

    low = text.lower()
    company_hit = None
    for cn in company_names:
        n = norm_name(cn)
        if len(n) >= 3:
            m = re.search(r"\b" + re.escape(n) + r"\b", re.sub(r"[^a-z0-9 ]+", " ", low))
            if m:
                company_hit = cn
                break

    city = core_city(person.get("city"))
    roles = role_terms_for(person.get("title"))
    best = None
    for s, e, _ in hits:
        a, b = max(0, s - window), min(len(text), e + window)
        win = low[a:b]
        near_role = next((r for r in roles if re.search(r"\b" + re.escape(r) + r"\b", win)), None)
        near_city = city if city and len(city) >= 3 and re.search(r"\b" + re.escape(city) + r"\b", win) else None
        adverse = [m.group() for t in ADVERSE_TERMS for m in [re.search(r"\b" + t + r"\b", win)] if m]
        ctx = []
        if company_hit:
            ctx.append(f"company \"{company_hit}\"")
        if near_role:
            ctx.append(f"role \"{near_role}\"")
        if near_city:
            ctx.append(f"city \"{near_city}\"")
        side = party_role(low, s, e)
        cand = (bool(ctx), bool(adverse), ctx, adverse, s, e, side)
        if best is None or cand[:2] > best[:2]:
            best = cand

    has_ctx, has_adv, ctx, adverse, s, e, side = best
    snippet = passage(text, s, e)
    if has_adv and side[0] and not side[1]:
        # Named in the case, but on the other side of it: being in a lawsuit isn't wrongdoing.
        return ("mentioned_not_adverse",
                f"\"{name}\" appears as {side[0]} (\"{', '.join(adverse)}\" refers to the case, not to "
                f"wrongdoing by them); no accusation term near the name", snippet)
    if not has_ctx:
        why = [f"company ({', '.join(company_names)})"]
        why.append(f"role ({person.get('title') or 'unknown'})")
        why.append(f"city ({city or 'unknown'})")
        return ("namesake_cleared",
                f"mentions \"{name}\" but not their {' / '.join(why)}, so treated as a namesake", snippet)
    if not has_adv:
        return ("no_adverse_content",
                f"mentions \"{name}\" with {', '.join(ctx)}, but no adverse term near the name", snippet)
    if all(DISPUTE_ONLY.match(t) for t in adverse):
        # A plain lawsuit / FIR / dispute isn't an accusation of fraud or a crime: a human reads it.
        return ("possible_adverse",
                f"mentions \"{name}\" with {', '.join(ctx)} near \"{', '.join(adverse)}\": a dispute or case, "
                f"not an accusation of fraud or arrest; needs a human to read it", snippet)
    if company_hit:
        return ("adverse", f"mentions \"{name}\" with {', '.join(ctx)} and \"{', '.join(adverse)}\" near the name",
                snippet)
    return ("possible_adverse",
            f"mentions \"{name}\" with {', '.join(ctx)} and \"{', '.join(adverse)}\" near the name, but not the "
            f"company, so a same-name person can't be ruled out; needs a human to read it", snippet)
