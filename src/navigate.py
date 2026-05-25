# /// script
# requires-python = ">=3.11"
# dependencies = ["beautifulsoup4", "playwright"]
# ///
"""Staged job-board navigator — the reasoning core of the scraper.

Companies expose jobs every which way: on-page lists, per-posting links to an ATS
(Greenhouse/Lever), a single "View Jobs" button to an ATS on another domain
(Workday/ADP/Rippling/Lever), internal gateway subpages, LinkedIn/Indeed, recruiter
aggregators with pagination, or nothing. No fixed ruleset survives that — so this
navigates in stages (website → jobs page → postings), deciding at each hop:

  render (Playwright = eyes) -> route (heuristics, then LLM when uncertain = brain)
  -> follow one hop / paginate / extract / external-pointer -> validate every URL.

Heuristic-first keeps it cheap: pages with obvious direct-posting links or known
external hosts need no LLM; the LLM reasons only on the ambiguous/JS/custom pages.

The LLM path uses src/llm.py (OAuth via the gemini CLI on the producer box).
"""

import asyncio
import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

# ---- budgets ----------------------------------------------------------------
MAX_HOPS = 2            # careers page -> board -> (board's board)
MAX_FOLLOW = 3          # links followed per page
MAX_PAGES = 6           # pagination pages per board
MAX_JOBS = 25           # postings kept per company
DESCRIPTION_CAP = 1500

# ---- URL/host knowledge -----------------------------------------------------
# Direct individual-posting URL shapes.
JOB_POST_PATTERNS = [
    r"boards?\.greenhouse\.io/[^/]+/jobs/\d+",
    r"job-boards\.greenhouse\.io/[^/]+/jobs/\d+",
    r"jobs\.lever\.co/[^/]+/[0-9a-fA-F-]{8,}",
    r"[^/]+\.bamboohr\.com/careers/\d+",
    r"ashbyhq\.com/[^/]+/[0-9a-fA-F-]{8,}",
    r"myworkdayjobs\.com/.+/job/.+",
    r"recruitee\.com/o/[^/]+",
    r"workable\.com/j/[A-Z0-9]+",
    r"smartrecruiters\.com/[^/]+/\d+",
    r"icims\.com/jobs/\d+",
    r"adp\.com/.*jobId=\d+",
    r"/jobs?/\d+", r"/careers?/\d+", r"/job/[^/?#]+-\d+", r"/vacanc(y|ies)/[^/?#]+",
    r"/position/[^/?#]+", r"/opening/[^/?#]+", r"jobId=\d+", r"requisition",
]

# Hosts that ARE a job board worth following into (not a single posting).
ATS_BOARD_HOSTS = [
    "myworkdayjobs.com", "lever.co", "greenhouse.io", "bamboohr.com",
    "ashbyhq.com", "rippling.com", "adp.com", "workforcenow.adp.com",
    "recruiting.adp.com", "icims.com", "recruitee.com", "workable.com",
    "smartrecruiters.com", "breezy.hr", "dayforcehcm.com", "taleo.net",
    "jobvite.com", "applytojob.com", "bullhornstaffing.com", "paylocity.com",
    "ultipro.com", "isolvedhire.com", "applicantpro.com",
]

EXTERNAL_HOSTS = {"linkedin.com": "linkedin", "indeed.com": "indeed",
                  "glassdoor.com": "glassdoor", "ziprecruiter.com": "ziprecruiter"}

BOARD_TAGS = {
    "greenhouse.io": "greenhouse", "lever.co": "lever", "bamboohr.com": "bamboohr",
    "ashbyhq.com": "ashby", "myworkdayjobs.com": "workday", "adp.com": "adp",
    "rippling.com": "rippling", "recruitee.com": "recruitee", "workable.com": "workable",
    "icims.com": "icims", "smartrecruiters.com": "smartrecruiters", "taleo.net": "taleo",
    "breezy.hr": "breezy", "linkedin.com": "linkedin", "indeed.com": "indeed",
}

# Link text suggesting a "go to the jobs board" CTA.
CTA_TEXT = ["view job", "view our", "open position", "current opportunit",
            "current opening", "see all job", "view all", "browse", "job board",
            "join our team", "we're hiring", "apply", "openings", "vacanc",
            "opportunities", "careers portal", "search jobs"]

GENERIC_TITLES = {"read more", "read more >", "apply", "apply now", "view", "view job",
                  "view details", "details", "learn more", "see more", "more", "→",
                  "view position", "open", "see details", "view opening", "view role"}


def canon_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/").lower()


def host_of(url: str) -> str:
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def is_job_post(url: str) -> bool:
    u = url.lower()
    return any(re.search(p, u) for p in JOB_POST_PATTERNS)


def is_board_host(url: str) -> bool:
    return any(frag in url.lower() for frag in ATS_BOARD_HOSTS)


def external_kind(url: str) -> str | None:
    h = host_of(url)
    for frag, kind in EXTERNAL_HOSTS.items():
        if frag in h:
            return kind
    return None


def source_board(url: str) -> str:
    lo = url.lower()
    for frag, label in BOARD_TAGS.items():
        if frag in lo:
            return label
    return "own-site"


JUNK_TITLES = {"careers", "career", "jobs", "job", "open positions", "openings",
               "work with us", "join us", "join our team", "home", "current openings"}


def best_title(anchor_title: str, page_title: str) -> str:
    a = (anchor_title or "").strip()
    if a.lower() in GENERIC_TITLES or len(a) < 4 or not re.search(r"[A-Za-z]{3,}", a):
        cand = re.split(r"\s[|\-–—]\s", (page_title or "").strip())[0].strip()
        return cand or a
    return a


def pick_title(anchor: str, h1: str, page_title: str) -> str:
    """Prefer the posting page's <h1> (clean role name) over board link text
    like 'Development VP of Software Engineering Canada, Remote'."""
    h = re.sub(r"\s+", " ", (h1 or "")).strip()
    if h and 3 <= len(h) <= 100 and h.lower() not in JUNK_TITLES:
        return h
    return best_title(anchor, page_title)


# Member companies that are recruiters/aggregators (jobs listed are for OTHER
# employers) — tag their listings via=<company> regardless of LLM flagging.
KNOWN_RECRUITERS = {"venor", "kbrs", "meridia", "higgins", "people store",
                    "axis career", "association for new canadians", "anc"}


def is_recruiter(company_name: str) -> bool:
    lo = company_name.lower()
    return any(h in lo for h in KNOWN_RECRUITERS)


# ---- rendering --------------------------------------------------------------
async def render(context, url: str) -> dict | None:
    """Load a page in a real browser; return rendered links + text + final URL."""
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=35000)
        # Heavy ATS SPAs (Workday/Lever/Rippling) populate listings late — wait for
        # the network to settle, then give it a beat, instead of a flat short sleep.
        try:
            await page.wait_for_load_state("networkidle", timeout=9000)
        except Exception:
            pass
        await asyncio.sleep(3.0)
        final = str(page.url)
        anchors = await page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href]'))"
            ".map(a => ({t:(a.innerText||'').replace(/\\s+/g,' ').trim(), h:a.href}))"
            ".filter(a => a.h)")
        text = await page.evaluate("() => document.body.innerText || ''")
        return {"final_url": final, "links": anchors, "text": (text or "")[:6000]}
    except Exception as e:
        print(f"   render failed {url}: {e}")
        return None
    finally:
        try:
            await page.close()
        except Exception:
            pass


# ---- heuristic routing ------------------------------------------------------
def heuristic_route(rendered: dict) -> dict | None:
    """Cheap decision when the page is unambiguous; else None -> ask the LLM."""
    final = rendered["final_url"]
    links = rendered["links"]

    # Direct individual postings on the page → extract them, no LLM.
    posts = []
    seen = set()
    for a in links:
        u = urljoin(final, a["h"])
        if is_job_post(u) and canon_url(u) not in seen:
            seen.add(canon_url(u))
            posts.append({"title": a["t"], "url": u})
    if posts:
        return {"jobs": posts}

    # We've landed on an external network (LinkedIn/Indeed) → pointer.
    if external_kind(final):
        return {"external": external_kind(final)}

    # A link out to a known ATS board (not itself a posting) → follow it, no LLM.
    # The followed board's posting URLs match JOB_POST_PATTERNS and get extracted
    # heuristically on the next hop (handles Workday/Lever/Greenhouse/etc.).
    boards, bseen = [], set()
    for a in links:
        u = urljoin(final, a["h"])
        if is_board_host(u) and not is_job_post(u) and host_of(u) != host_of(final):
            k = canon_url(u)
            if k not in bseen:
                bseen.add(k)
                boards.append(u)
    if boards:
        return {"follow": boards[:MAX_FOLLOW]}

    return None  # genuinely ambiguous (custom/JS/on-page list) → LLM


# ---- LLM routing ------------------------------------------------------------
ROUTER_SYSTEM = ("You analyze a rendered company careers/jobs page and decide how "
                 "its job postings are exposed. Use ONLY URLs present in the input. "
                 "Never invent URLs. Respond with one JSON object, no prose.")


def _router_prompt(company: str, rendered: dict) -> str:
    links = rendered["links"][:140]
    listing = "\n".join(f"- {a['t'][:80]} | {a['h']}" for a in links if a["t"] or a["h"])
    return (
        f"Company: {company}\nPage URL: {rendered['final_url']}\n"
        f"Visible text (excerpt):\n{rendered['text'][:1500]}\n\n"
        f"Links on the page:\n{listing}\n\n"
        "Respond with JSON:\n"
        "{\n"
        '  "jobs": [{"title": "<role>", "url": "<posting url from links>"}],   // individual postings identifiable now\n'
        '  "follow": ["<url>"],        // board/ATS/subpage URLs to open for the actual listings\n'
        '  "next_page": "<url|null>",  // pagination link to more results\n'
        '  "is_aggregator": false,     // true if this is a recruiter/job-board listing roles for OTHER companies\n'
        '  "external": null            // "linkedin"|"indeed" if jobs live only there\n'
        "}\n"
        "Rules: put a role in \"jobs\" only if you can map it to a real posting URL "
        "from the links. If the page is just a button to an ATS, leave \"jobs\" empty "
        "and put that ATS URL in \"follow\"."
    )


def parse_router(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


async def llm_route(company: str, rendered: dict) -> dict:
    from llm import llm_call, LLMError
    try:
        out = llm_call(_router_prompt(company, rendered), system=ROUTER_SYSTEM, timeout=90)
    except LLMError as e:
        print(f"   LLM route unavailable: {e}")
        return {}
    return parse_router(out)


# ---- validation gate --------------------------------------------------------
async def validate_and_describe(context, company: str, candidates: list[dict],
                                board_urls: set[str]) -> list[dict]:
    """Visit each candidate posting: drop 404s / redirects-to-home / the board page
    itself / landing pages; keep real postings with a title + description."""
    out = []
    for c in candidates[:MAX_JOBS]:
        url = c["url"]
        page = None
        try:
            page = await context.new_page()
            await page.route("**/*.{png,jpg,jpeg,gif,webp,css,svg,woff2,mp4}",
                             lambda r: r.abort())
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(0.8)
            final = str(page.url)
            status = resp.status if resp else 0
            # reject: bad status, bounced to a root/landing/board page
            if status >= 400:
                continue
            path = urlparse(final).path.rstrip("/")
            if path in ("", "/"):                       # redirected to homepage
                continue
            if canon_url(final) in board_urls:           # just the board, not a posting
                continue
            data = await page.evaluate(
                "() => ({h1:(document.querySelector('h1')?document.querySelector('h1').innerText:''),"
                " title:document.title||'', text:document.body.innerText||''})")
            text = (data.get("text") or "").strip()
            if len(text) < 120:                          # empty/non-posting
                continue
            title = pick_title(c.get("title", ""), data.get("h1"), data.get("title"))
            rec = {
                "company": company,
                "title": re.sub(r"\s+", " ", title)[:140],
                "url": url,
                "location": c.get("location"),
                "source_board": source_board(url),
                "description": text[:DESCRIPTION_CAP],
            }
            if c.get("via_recruiter"):
                rec["via"] = company  # listed via this member acting as recruiter
            out.append(rec)
            await asyncio.sleep(0.3)
        except Exception:
            continue
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
    return out


# ---- the navigator ----------------------------------------------------------
async def navigate_company(context, company: dict, llm_enabled: bool = True) -> dict:
    """Return {'jobs': [...], 'pointer': {...}|None, 'scraped_ok': bool}."""
    name = company["company_name"]
    start = company["career_page_url"]

    visited: set[str] = set()
    board_urls: set[str] = set()
    candidates: list[dict] = []
    pointer = None
    pages_used = 0
    scraped_ok = False

    queue: list[tuple[str, int]] = [(start, 0)]
    while queue:
        url, hop = queue.pop(0)
        key = canon_url(url)
        if key in visited:
            continue
        visited.add(key)

        rendered = await render(context, url)
        if rendered is None:
            continue
        scraped_ok = True
        board_urls.add(canon_url(rendered["final_url"]))

        # Anti-bot wall (CAPTCHA / challenge) — record a pointer, don't waste an LLM call.
        flo = rendered["final_url"].lower()
        if any(m in flo for m in ("captcha", "/challenge", "cloudflare")) or \
                (len(rendered["links"]) <= 1 and len(rendered["text"]) < 200):
            pointer = {"company": name, "kind": "blocked", "url": rendered["final_url"]}
            continue

        route = heuristic_route(rendered)
        if route is None and llm_enabled:
            route = await llm_route(name, rendered)
        route = route or {}

        if route.get("external"):
            pointer = {"company": name, "kind": route["external"],
                       "url": rendered["final_url"]}
            continue

        agg = bool(route.get("is_aggregator")) or is_recruiter(name)
        for j in (route.get("jobs") or []):
            ju = j.get("url")
            if ju:
                candidates.append({"title": j.get("title", ""),
                                   "url": urljoin(rendered["final_url"], ju),
                                   "via_recruiter": agg})

        if hop < MAX_HOPS:
            for f in (route.get("follow") or [])[:MAX_FOLLOW]:
                fu = urljoin(rendered["final_url"], f)
                if canon_url(fu) not in visited:
                    queue.append((fu, hop + 1))

        nxt = route.get("next_page")
        if nxt and pages_used < MAX_PAGES:
            pages_used += 1
            nu = urljoin(rendered["final_url"], nxt)
            if canon_url(nu) not in visited:
                queue.append((nu, hop))  # same hop depth for pagination

        if len(candidates) >= MAX_JOBS * 2:
            break

    # dedup candidates by canonical URL
    seen, deduped = set(), []
    for c in candidates:
        k = canon_url(c["url"])
        if k not in seen:
            seen.add(k)
            deduped.append(c)

    jobs = await validate_and_describe(context, name, deduped, board_urls)
    print(f"  {name}: {len(jobs)} validated job(s)"
          + (f" + {pointer['kind']} pointer" if pointer else ""))
    return {"jobs": jobs, "pointer": pointer, "scraped_ok": scraped_ok}
