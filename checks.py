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


def name_similarity(a, b):
    """0-100. token_set_ratio treats 'Shadowfax Technologies Pvt Ltd' and 'Shadowfax' as the same name."""
    a, b = norm_name(a), norm_name(b)
    if not a or not b:
        return 0
    return fuzz.token_set_ratio(a, b)


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
                "unusual traffic from your computer"]


def is_interstitial(title, text):
    """A bot-check / redirect page instead of the real one. Its content says nothing about the target."""
    t, low = (title or "").strip().lower(), (text or "")[:1500].lower()
    if t in ("google", "just a moment...", "attention required! | cloudflare", "access denied"):
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
                 r"fir (?:against|registered)", r"ponzi", r"abscond\w*", r"forger\w*"]
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
    if company_hit:
        return ("adverse", f"mentions \"{name}\" with {', '.join(ctx)} and \"{', '.join(adverse)}\" near the name",
                snippet)
    return ("possible_adverse",
            f"mentions \"{name}\" with {', '.join(ctx)} and \"{', '.join(adverse)}\" near the name, but not the "
            f"company, so a same-name person can't be ruled out; needs a human to read it", snippet)
