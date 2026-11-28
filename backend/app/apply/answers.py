"""Decide what goes in each field: profile rules first, then remembered answers, then Claude."""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from .. import llm
from .. import profile as prof
from ..llm import B, S, arr, obj
from ..util import US_STATES

log = logging.getLogger("autoapply.answers")

DECLINE = ["decline to self-identify", "i don't wish to answer", "i do not wish to answer", "prefer not to say",
           "prefer not to answer", "decline to answer", "choose not to disclose", "decline"]


def _n(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+ ]", " ", (s or "").lower())).strip()


def _word_in(needle: str, hay: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", hay))


INSIDER = re.compile(r"\b(internal|employee|referral|referred|current (staff|rioter|team member))\b", re.I)


def pick_option(desired: str | list[str], options: list[str]) -> str | None:
    """Best option for a desired answer. Word-boundary aware ('Male' never matches 'Female').
    Never lands on an "internal / employee referral" option unless that is what was asked for."""
    if not options:
        return None
    cands = [desired] if isinstance(desired, str) else list(desired)
    cands = [str(c) for c in cands if c]
    if not cands:
        return None
    if not any(INSIDER.search(c) for c in cands):
        options = [o for o in options if not INSIDER.search(o)] or options
    # "Other" is a last resort: only when no real candidate matches, exactly or fuzzily.
    if any(_n(c) == "other" for c in cands) and len(cands) > 1:
        real = [c for c in cands if _n(c) != "other"]
        return pick_option(real, options) or next((o for o in options if _n(o) == "other"), None)
    nopts = [_n(o) for o in options]
    for c in cands:
        nc = _n(c)
        for o, no in zip(options, nopts):
            if no == nc:
                return o
    for c in cands:
        nc = _n(c)
        # yes/no family
        if nc in ("yes", "no") or nc.startswith(("yes ", "no ")):
            yn = nc.split()[0]
            for o, no in zip(options, nopts):
                if no == yn or no.startswith(yn + " "):
                    return o
        # "decline" family
        if any(d in nc for d in ("decline", "don t wish", "do not wish", "prefer not", "not to disclose", "choose not")):
            for o, no in zip(options, nopts):
                if any(d in no for d in ("decline", "don t wish", "do not wish", "prefer not", "not to disclose",
                                         "choose not", "not wish to", "rather not", "do not want", "don t want")):
                    return o
        # "I am not a protected veteran" / "No, I do not have a disability" on a yes/no list
        if nc.startswith(("i am not", "no i do not", "i do not")):
            for o, no in zip(options, nopts):
                if no in ("no",) or no.startswith(("i am not", "no i", "not a")):
                    return o
        # Fuzzy passes refuse ambiguity: "University of Maryland" must not land on whichever campus sorts first.
        longer = [o for o, no in zip(options, nopts) if no and no.startswith(nc) and len(nc) >= 2]
        if len(longer) == 1:
            return longer[0]
        shorter = [(len(no), o) for o, no in zip(options, nopts) if no and len(no) >= 2 and nc.startswith(no)]
        if shorter and not longer:
            return max(shorter)[1]
        if len(nc) >= 3:
            inside = [o for o, no in zip(options, nopts) if no and _word_in(nc, no)]
            if len(inside) == 1:
                return inside[0]
            contained = [(len(no), o) for o, no in zip(options, nopts) if no and len(no) >= 3 and _word_in(no, nc)]
            if contained and not inside:
                return max(contained)[1]
    # token overlap, only with a clear winner
    scores = []
    for c in cands:
        ct = set(_n(c).split())
        if not ct:
            continue
        for o, no in zip(options, nopts):
            ot = set(no.split())
            if ot:
                scores.append((len(ct & ot) / len(ct | ot), o))
    scores.sort(key=lambda x: -x[0])
    if scores and scores[0][0] >= 0.5 and (len(scores) == 1 or scores[0][0] > scores[1][0] or scores[0][1] == scores[1][1]):
        return scores[0][1]
    return None


# ---------------- rules ----------------

FLAGSHIP = {  # bare system name on a resume -> the main campus people almost always mean
    "university of maryland": "University of Maryland, College Park",
    "university of michigan": "University of Michigan, Ann Arbor",
    "university of texas": "University of Texas at Austin",
    "university of illinois": "University of Illinois Urbana-Champaign",
    "university of wisconsin": "University of Wisconsin-Madison",
    "university of minnesota": "University of Minnesota, Twin Cities",
    "university of washington": "University of Washington, Seattle",
    "university of colorado": "University of Colorado Boulder",
    "university of massachusetts": "University of Massachusetts Amherst",
    "university of north carolina": "University of North Carolina at Chapel Hill",
    "penn state": "Pennsylvania State University, University Park",
    "pennsylvania state university": "Pennsylvania State University, University Park",
    "rutgers": "Rutgers University, New Brunswick",
    "rutgers university": "Rutgers University, New Brunswick",
    "purdue university": "Purdue University, West Lafayette",
    "indiana university": "Indiana University Bloomington",
    "university of pittsburgh": "University of Pittsburgh, Pittsburgh",
}


def school_candidates(school: str) -> list[str]:
    s = (school or "").strip()
    if not s:
        return []
    full = FLAGSHIP.get(_n(s), s)
    out = [full]
    if "," in full:
        a, b = full.split(",", 1)
        out += [f"{a} - {b.strip()}", f"{a}-{b.strip()}", f"{a} {b.strip()}", f"{a} ({b.strip()})"]
    if " at " in full:
        out.append(full.replace(" at ", ", "))
    if full != s:
        out.append(s)   # last: the bare name only matches an option that is exactly that
    return list(dict.fromkeys(out))

def degree_candidates(deg: str) -> list[str]:
    d = (deg or "").lower().replace(".", "").strip()
    if not d:
        return []
    if re.match(r"^(bs|bsc|ba|bachelor|b s|b a|bse|bba)", d) or "bachelor" in d:
        sci = "science" in d or d.startswith(("bs", "bsc", "bse"))
        return ["Bachelor's Degree", "Bachelor's", "Bachelors", "Bachelor of Science" if sci else "Bachelor of Arts", "BS" if sci else "BA", deg]
    if re.match(r"^(ms|msc|ma|master|meng|mba)", d) or "master" in d:
        return ["Master's Degree", "Master's", "Masters", "MBA" if "mba" in d else "Master of Science", deg]
    if re.match(r"^(phd|doctor)", d) or "doctor" in d:
        return ["Doctorate", "PhD", "Ph.D.", "Doctor of Philosophy", deg]
    if "associate" in d:
        return ["Associate's Degree", "Associate's", deg]
    return [deg]

# Yes/no questions backed by one profile field. Your answer to any wording (in Needs you, or typed in the browser)
# fills the field, so every other wording is covered from then on.
OVER18_Q = r"18 years|at least 18|over 18|over the age|of legal age|age of 18"
FELONY_Q = r"felony|misdemeanor|convicted|conviction|criminal (conviction|offense|record|history|background)"
LICENSE_Q = r"driver'?s licen[cs]e"
DISMISSED_Q = r"terminated|dismissed|asked to resign|discharged|fired"
TOBACCO_Q = r"tobacco|nicotine"
NONCOMPETE_Q = r"non-?compete|non-?solicit"
PROFILE_YES_NO = [(FELONY_Q, "felony"), (DISMISSED_Q, "dismissed"), (LICENSE_Q, "drivers_license"),
                  (TOBACCO_Q, "tobacco"), (NONCOMPETE_Q, "non_compete"), (OVER18_Q, "over_18")]
# Reversed wording ("a clean record?", "tobacco-free?"): the profile's Yes/No would mean the opposite.
INVERTED_Q = r"\b(clean|free (of|from)|non-?smok\w*|non-?users?|not been|never been|no (criminal|record|convictions?))\b|\w-free\b"


def to_yes_no(answer) -> str:
    """"No, I have not been convicted..." -> "No"; "" when the answer isn't a plain yes or no."""
    a = str(answer or "").strip().lower()
    if re.match(r"(n/?a|not applicable|prefer not|decline)\b", a):
        return ""
    if re.match(r"(yes|y|true|i (do|am|have)(?! not| never))\b", a):
        return "Yes"
    if re.match(r"(no|n|false|none|never|i (do not|don't|am not|have not|haven't|have never))\b", a):
        return "No"
    return ""


def profile_field_for(question: str) -> str | None:
    q = (question or "").lower()
    if re.search(INVERTED_Q, q):
        return None
    return next((field for pat, field in PROFILE_YES_NO if re.search(pat, q)), None)


def remember_answer(question: str, answer) -> None:
    """Keep your answer to a question for next time. A yes/no one backed by a profile field also fills that field
    (if it's empty), so differently worded versions of the question get the same answer."""
    prof.remember(question, str(answer))
    el = prof.get()["eligibility"]
    field, yn = profile_field_for(question), to_yes_no(answer)
    if field and yn and not el.get(field):
        prof.save({"eligibility": {field: yn}})
    elif re.search(r"salary|compensation|desired pay|pay expectation", (question or "").lower()) \
            and str(answer).strip() and not el.get("salary_expectation"):
        prof.save({"eligibility": {"salary_expectation": str(answer).strip()}})


def _yn(v: str) -> str:
    v = (v or "").strip()
    return v if v in ("Yes", "No") else ""


def _latest(lst: list[dict]) -> dict:
    return lst[0] if lst else {}


def rule_answer(f: dict, p: dict, job: dict) -> tuple[str | list | None, str]:
    """Returns (value, source). value None = no rule applies."""
    q = (f.get("question") or "").lower()
    meta = f"{q} {f.get('name','').lower()} {f.get('autocomplete','').lower()}"
    kind = f["kind"]
    c, e, eeo = p["contact"], p["eligibility"], p["eeo"]
    exp, edu = _latest(p["experience"]), _latest(p["education"])
    has = lambda pat: re.search(pat, q)
    is_choice = kind in ("select", "radio", "buttons", "listbox", "combobox", "checkboxes", "multiselect")
    # Contact/profile rules only fire on short, label-like questions: an essay that happens to mention
    # "website" or contain a URL must go to Claude, never get a bare link.
    short = kind != "textarea" and len(q) <= 80
    yn_ok = is_choice or short
    high_school = bool(has(r"high school|secondary school|\bged\b|\bhs\b"))
    from ..util import NON_US, is_us
    non_us_job = is_us(job.get("location") or "", bool(job.get("remote"))) is False or bool(NON_US.search(q))
    current_roles = [x for x in p["experience"] if x.get("current")]

    if kind == "file":
        if re.search(r"autofill|auto-fill|parse your resume", q):
            return "__AUTOFILL__", "profile"   # let the site read the resume first, then fill what it missed
        if re.search(r"cover\s*letter", q):
            return "__COVER__", "generated"
        if re.search(r"resume|\bcv\b|curriculum", q) or not q.strip():
            return "__RESUME__", "profile"
        helper = r"attach|upload( a)?( file)?|choose( a)? file|select( a)? files?|drag (and|&) drop( here)?|drop (files? )?here|browse|files?"
        generic = re.sub(helper, " ", q)                       # "Drop file hereorSelect file" -> "  or  "
        generic = re.sub(r"\b(or|and|here|a|your)\b", " ", generic)
        if not generic.strip(" .,:*-()"):
            return "__RESUME__", "profile"
        return None, ""

    if kind == "checkbox":
        if re.search(r"marketing|newsletter|future (opportunit|job|role)|job alert|talent (community|network|pool)|subscribe|sms|text message|whatsapp", q):
            return "", "rule"
        if f.get("required") or re.search(r"\b(i agree|agree to|acknowledge|certify|consent|i confirm|confirm that|i have read|privacy|terms)", q):
            return "checked", "rule"
        return None, ""

    if not short and not is_choice:
        return None, ""

    # Before the contact rules: "opt in to receive emails" is a yes/no question, not a request for your email address.
    if is_choice and has(r"\bopt[- ]?in\b|marketing|newsletter|job alerts?|talent (community|network|pool)|"
                         r"(occasional|promotional) (messages|e-?mails|communications|updates)"):
        # Marketing and future-job mailing lists: decline unless you've said otherwise. It doesn't affect the application.
        r = prof.recall(f["question"])
        return (r, "remembered") if r else (["No", "No thanks", "I do not wish to receive", "Do not opt in", "Decline"], "rule")

    # identity / contact
    if re.search(r"given-name", meta) or has(r"\b(legal )?first name\b|given name"):
        return c["first_name"], "profile"
    if has(r"preferred (first )?name|nickname|name you go by"):
        return c.get("preferred_name") or c["first_name"], "profile"
    if has(r"middle name"):
        return "", "profile"
    if re.search(r"family-name", meta) or has(r"\blast name\b|family name|surname"):
        return c["last_name"], "profile"
    if has(r"^(your |full |legal |full legal )?name$|^full (legal )?name|^applicant name$"):
        return f"{c['first_name']} {c['last_name']}".strip(), "profile"
    if has(r"e-?mail") and not has(r"referr|recruiter|manager"):
        return c["email"], "profile"
    if has(r"phone|\bmobile\b|telephone|\bcell\b"):
        if has(r"device|type"):
            return ["Mobile", "Cell"], "rule"
        if has(r"country|code") and is_choice:
            return ["United States of America (+1)", "United States (+1)", "+1", "United States", "US +1"], "rule"
        if has(r"extension"):
            return "", "rule"
        digits = re.sub(r"\D", "", c["phone"])
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if has(r"whatsapp|international|with country code|incl(uding)? country code") and digits:
            return f"+1 {digits[:3]}-{digits[3:6]}-{digits[6:]}", "profile"
        return (digits if kind == "text" and f.get("inputType") == "tel" else c["phone"]) or None, "profile"
    if has(r"address line 2|address 2|apartment|suite"):
        return "", "profile"
    if has(r"address line 1|street|address 1"):
        return c["address"] or None, "profile"
    if has(r"^(current |full |home |mailing |your )?(full )?address\b|mailing address|home address|current (full )?address"):
        full = ", ".join(x for x in [c["address"], c["city"], f"{c['state']} {c['zip']}".strip(), c["country"]] if x)
        return full or None, "profile"
    if has(r"\bcity\b") and not has(r"state|citizenship"):
        return c["city"] or None, "profile"
    if has(r"\bzip|postal|post ?code"):
        return c["zip"] or None, "profile"
    if has(r"\b(state|province|region)\b") and not has(r"united states|statement|state of|states|authorized"):
        st = c["state"]
        full = US_STATES.get(st.upper(), st) if st else ""
        return [full.title(), st] if st else None, "profile"
    if has(r"^country$|country of residence|^country\b") and not has(r"citizen|phone"):
        return ["United States of America", "United States", "USA", "US"] if (c["country"] or "").lower() in ("united states", "usa", "us", "united states of america") else c["country"], "profile"
    if has(r"^location|current location|where are you (currently )?(located|based)|city,? state|location \(city\)|where do you live|address ?/ ?location|location ?/ ?address"):
        loc = ", ".join(x for x in [c["city"], c["state"]] if x)
        return loc or None, "profile"
    if has(r"linkedin"):
        return prof.normalize_url(c["linkedin"]) or None, "profile"
    if has(r"github"):
        return prof.normalize_url(c["github"]) or None, "profile"
    if has(r"website|portfolio|personal (site|url|page)|blog"):
        # GitHub stands in for a portfolio only when the field is required; otherwise it just duplicates the GitHub box.
        return prof.normalize_url(c["website"] or (c["github"] if f.get("required") else "")) or "", "profile"
    if has(r"twitter|x\.com|instagram|facebook"):
        return "", "rule"

    if kind in ("text", "textarea") and re.match(r"\s*(if\b|please (specify|explain|describe|elaborate|list))", q):
        return None, ""   # conditional follow-ups ("If yes, explain...") need judgment, not a profile field

    # work & school
    if high_school:
        return None, ""   # the profile has no high-school data; never reuse college facts here
    if has(r"most recent (company|employer)|current or most recent (company|employer)"):
        return e.get("current_employer") or exp.get("company") or None, "profile"
    if has(r"current (company|employer)|current organization|where do you (currently )?work"):
        return e.get("current_employer") or (current_roles[0]["company"] if current_roles else None), "profile"
    if has(r"most recent (job )?title|current or most recent (job )?title"):
        return exp.get("title") or None, "profile"
    if has(r"current (job )?title|current role|current position"):
        return (current_roles[0]["title"] if current_roles else None), "profile"
    if has(r"\b(school|university|college|institution)\b") and not has(r"graduat|year|degree"):
        return school_candidates(edu.get("school") or "") or None, "profile"
    if has(r"\bdegree\b") and not has(r"graduat|year|date|completed|status|obtain"):
        return degree_candidates(edu.get("degree", "")) or None, "profile"
    if has(r"discipline|\bmajor\b|field of study|area of study") and not has(r"majority"):
        fld = edu.get("field") or ""
        return ([fld, re.split(r",|;| minor| with ", fld)[0].strip()] if fld else None), "profile"
    if has(r"\bgpa\b|grade point"):
        return edu.get("gpa") or None, "profile"
    if has(r"graduation|grad date|expected to graduate|expected graduation|completion date|anticipate graduating|graduating year|year of graduation"):
        return re.sub(r"\s*\(.*?\)", "", edu.get("end") or "").strip() or None, "profile"

    # eligibility (US-only facts; anything about another country goes to Claude, which will ask you)
    if not yn_ok:
        return None, ""
    if non_us_job and has(r"sponsor|authori[sz]ed|eligible to work|right to work|work permit|visa"):
        return None, ""
    if has(r"sponsor"):
        return _yn(e["need_sponsorship"]) or None, "profile"
    if has(r"authori[sz]ed to work|legally (authori[sz]ed|eligible|able)|eligible to work|work authori[sz]ation|right to work|legally permitted"):
        return _yn(e["authorized_us"]) or None, "profile"
    if has(r"(visa|immigration|citizenship|work) status") and is_choice:
        return e.get("work_status") or None, "profile"
    if has(r"relocat") and has(r"assist|package|support|reimburse"):
        return _yn(e["relocation_assistance"]) or None, "profile"
    if has(r"relocat"):
        return _yn(e["willing_relocate"]) or None, "profile"
    if has(r"hybrid|on-?site|in[- ]office|in person|commut") and is_choice and set(_n(o) for o in f.get("options", [])) <= {"yes", "no"}:
        return _yn(e["open_hybrid_onsite"]) or None, "profile"
    if has(OVER18_Q):
        return _yn(e.get("over_18", "")) or None, "profile"
    if has(r"^(total )?years of (professional |relevant |work )?experience|how many years of (professional |work )?experience do you have\??$"):
        return str(e["years_experience"]) if e["years_experience"] != "" else None, "profile"
    if has(r"start date|earliest.*start|when can you start|available to start|availability to start|date available") and not has(r"month|year|education|school|degree|employment history"):
        return e.get("start_date") or None, "profile"
    if has(r"notice period"):
        return e.get("notice_period") or None, "profile"
    if has(r"salary|compensation expectation|desired (pay|compensation)|pay expectation|expected (base|compensation|pay)"):
        return e.get("salary_expectation") or None, "profile"
    if has(r"text message|sms|ok to text|contact you (via|by) text|receive texts|\btext you\b"):
        return _yn(e["ok_to_text"]) or None, "profile"
    if has(FELONY_Q):
        # A legal statement about you: only your profile or your own earlier answer, never Claude's guess.
        if _yn(e["felony"]) and not has(INVERTED_Q):
            return _yn(e["felony"]), "profile"
        r = prof.recall(f["question"])
        return (r, "remembered") if r else ("", "needs_user")
    if has(LICENSE_Q) and not has(INVERTED_Q):
        return _yn(e["drivers_license"]) or None, "profile"
    if has(DISMISSED_Q) and not has(INVERTED_Q):
        return _yn(e["dismissed"]) or None, "profile"
    if has(TOBACCO_Q) and not has(INVERTED_Q):
        return _yn(e["tobacco"]) or None, "profile"
    if has(NONCOMPETE_Q) and not has(INVERTED_Q):
        return _yn(e["non_compete"]) or None, "profile"
    if has(r"security clearance|active clearance|hold a clearance|clearance level"):
        cl = e.get("clearance") or ""
        if is_choice:
            return ([cl] if cl else ["None", "No", "I do not have", "N/A"]), "profile"
        return cl or "None", "profile"
    if e.get("no_conflicts") and is_choice and (
            has(r"previously (worked|been employed|applied)|former employee|worked (at|for) .{0,40} before|ever (been )?(employed|worked)")
            or has(r"relative|family member|related to|know anyone|personal relationship|referred by an? (current )?employee")
            or has(r"government official|public official|government employee|military|finra|\bsec\b|sanction|debarred|regulator")
            or has(r"conflict of interest")):
        return "No", "profile"
    if has(r"how (did )?you (hear|heard|find|found|learn|learned|come across|came across)|where did you (hear|find|see|learn)|referral source|^source$|how were you referred"):
        r = prof.recall(f["question"])
        co = (job.get("company") or "").strip()
        own_site = [f"{co} Website", f"{co} Careers", f"{co} Careers Page", f"{co} Career Site"] if co else []
        # Only "found it on the company's site" or "Other": never claim a channel (LinkedIn, referral) that didn't happen.
        return ([r] if r else []) + own_site + ["Company Website", "Company website", "Career Site", "Careers Page",
                                                "Company Careers Page", "Corporate Website", "Website", "Other"], "remembered" if r else "rule"

    # voluntary self-identification
    if has(r"\bgender\b|\bsex\b") and not has(r"orientation|transgender"):
        return (eeo["gender"] or "Decline to self-identify") if is_choice else None, "profile"
    if has(r"hispanic|latin[oax]") and is_choice and len(f.get("options", [])) <= 4:
        return (eeo["hispanic"] or "Decline to self-identify"), "profile"
    if has(r"\brace\b|ethnicit"):
        v = eeo["race"] or "Decline to self-identify"
        cands = [v]
        if v in ("East Asian", "South Asian", "Southeast Asian"):
            cands += ["Asian (Not Hispanic or Latino)", "Asian"]
        return cands if is_choice else None, "profile"
    if has(r"veteran"):
        return (eeo["veteran"] or "I don't wish to answer") if is_choice else None, "profile"
    if has(r"disabilit"):
        return (eeo["disability"] or "I don't wish to answer") if is_choice else None, "profile"
    if has(r"sexual orientation"):
        return (eeo["orientation"] or "Decline to self-identify") if is_choice else None, "profile"
    if has(r"transgender"):
        return (eeo["transgender"] or "Decline to self-identify") if is_choice else None, "profile"
    if has(r"^(today'?s )?date$|signature date|date signed") and kind in ("text", "date"):
        return date.today().strftime("%m/%d/%Y"), "rule"
    if has(r"(electronic )?signature|sign your name|type your (full )?name"):
        return f"{c['first_name']} {c['last_name']}".strip(), "profile"
    if has(r"languages? (do )?you speak|language proficienc"):
        langs = ", ".join(l["name"] for l in p.get("languages") or [])
        return langs or None, "profile"
    return None, ""


# ---------------- Claude for everything else ----------------

ANSWER_SCHEMA = obj({"answers": arr(obj({"id": S, "value": S, "values": arr(S), "needs_user": B}))})

ANSWER_SYSTEM = """You fill out job application forms on behalf of the candidate described below. The candidate reviews nothing before it is sent, so accuracy matters more than completeness.

Hard rules:
- Use only facts from the candidate profile, remembered answers, and the job posting. Never invent employers, titles, degrees, dates, skills, numbers, awards, or experience. Never claim a skill the profile doesn't list.
- Choice fields (select, radio, buttons, listbox, combobox with options, checkboxes): answer with option text copied exactly from the given options. Use `values` for multi-select fields, `value` for single.
- Factual eligibility questions the profile doesn't answer (age, specific certifications, licenses, specific dates, ID numbers, referral names, availability for specific shifts, etc.): leave value "" and set needs_user=true if the field is required. Do not guess.
- Legal attestations about the candidate (criminal history, convictions, past discipline, debarment, background-check disclosures) that the profile doesn't answer: value "" and needs_user=true. Never assume the answer, even the common one.
- Opt-ins to marketing, newsletters, job alerts or future-opportunity messages: answer No (or leave unchecked) unless a remembered answer says otherwise.
- "Years of experience with X": use a remembered answer if one matches; otherwise derive only from dated roles in the profile, counting internships, and if the profile has no dated professional use of X answer "0" (or the lowest option). Never inflate.
- Open-ended questions (why this company, why this role, describe a project, tell us about yourself, additional information): write in first person, plain and specific, and answer the exact question asked. 60-150 words unless a character limit or the question implies shorter. No clichés, no buzzword strings, no em dashes, no exclamation marks.
- No embellishment. Restate profile facts conservatively. Do not add motivations, processes (e.g. "test-first"), feedback loops, users, collaborators, adjectives about past systems (e.g. "fragile"), lessons learned, or authorship ("I built the benchmark") that the profile does not state. Never recast what a project does or why it exists to fit the posting.
- Do not present the posting's requirements as things the candidate already does ("I use X daily") unless the profile says so. Connect instead: say what the candidate did (from the profile) and why this role interests them.
- "Why this company" answers must name something specific from the posting (product, team, problem, stated mission). If the posting gives little, keep the answer short and honest rather than generic.
- On one form, spread the material: don't retell the same project or repeat the same statistic in several answers; each answer uses what fits its own question best. Don't restate resume bullets word for word.
- Use absolute dates ("in summer 2025"), never relative ones ("last summer", "recently"); today's date is given below.
- Never invent a date part. If the profile gives only a year (e.g. start "2023"), a month field gets "" (needs_user only if required); never guess a month or day.
- Preference questions (which product, team, office, shift, start month): if the job posting itself settles it (the title or posting names the role, team or location being applied to), answer accordingly and explain briefly from the profile when the field asks why. Otherwise, if the options include something like "No preference", "Open to any" or "Flexible", pick it; otherwise value "" and needs_user=true if required. Never invent a preference the posting and profile don't support.
- Screening questions about experience ("Do you have experience with X?", "Which of these areas have you worked in?"): pick the option the profile clearly supports; if the evidence is indirect or missing, pick "No" / "None of the above" / the lowest level.
- Optional questions that add nothing (e.g. "anything else?", "pronouns" when the profile has none): value "".
- Salary or compensation questions when the profile has no salary expectation: value "" and needs_user=true if required, "" if optional. Never invent a number and never claim flexibility the profile doesn't state.
- Text fields with obvious formats (dates, numbers, URLs) must use that format. input_type=number means digits only (no commas, words or currency); if the profile has no number for it, leave "" (needs_user true if required).
- A hint line tells you what the profile says for a choice field whose options use different wording: pick the option with the same meaning, or "" if none does.
- needs_user: true only when the field is required and cannot be answered truthfully from the information given."""


def _profile_text(p: dict) -> str:
    import json
    keep = {k: p[k] for k in ("contact", "looking_for", "eligibility", "about", "languages", "experience",
                              "education", "projects", "skills")}
    keep["eeo"] = p["eeo"]
    facts = []
    el = p["eligibility"]
    if el.get("no_conflicts"):
        facts.append("The candidate confirms: never employed by or contracted to this company before; no relatives, friends "
                     "or partners working there; not referred by an employee; no government, military or public-official "
                     "background; never sanctioned by a regulator (FINRA, SEC, etc.). Answer such questions No / None.")
    if el.get("degree_status") and el["degree_status"] != "As on my resume":
        facts.append(f"Degree status: {el['degree_status']}.")
    if not el.get("current_employer") and not any(x.get("current") for x in p["experience"]):
        facts.append("The candidate has no current employer: every listed role has ended. For 'current company/title' "
                     "questions answer N/A (or the school if the form asks for a current organization).")
    facts.append("Work-authorization and sponsorship answers in the profile are for the United States only. For a job in "
                 "another country, the candidate's right to work there is unknown: value \"\" and needs_user=true.")
    out = json.dumps(keep, ensure_ascii=False)
    return out + ("\nFacts: " + " ".join(facts) if facts else "")


async def llm_answers(fields: list[dict], p: dict, job: dict) -> dict[str, dict]:
    if not fields:
        return {}
    remembered = prof.remembered_all()[:120]
    rem = "\n".join(f"Q: {r['question']}\nA: {r['answer']}" for r in remembered) or "none"
    lines = []
    for f in fields:
        o = f.get("options") or []
        if o and f["kind"] != "date":
            opts = f" options={o[:80]}" + (f" (+{len(o) - 80} more; if the right one isn't shown, give the plain value and it will be matched)" if len(o) > 80 else "")
        else:
            opts = ""
        lim = f" maxlength={f['maxlength']}" if f.get("maxlength") else ""
        it = f.get("inputType") or ""
        lim += f" input_type={it}" if it in ("number", "email", "url", "tel", "date", "month") else ""
        lim += f"\n  hint: {f['hint']}" if f.get("hint") else ""
        multi = " (multi-select)" if f["kind"] in ("checkboxes", "multiselect") or f.get("multiple") else ""
        lines.append(f"- id={f['id']} kind={f['kind']}{multi} required={str(f['required']).lower()}{lim}{opts}\n  question: {f['question'][:600]}")
    # The profile and remembered answers are the same on every form: cached, they cost ~1/20th after the first request.
    context = (f"<candidate_profile>\n{_profile_text(p)}\n</candidate_profile>\n\n"
               f"<remembered_answers>\n{rem}\n</remembered_answers>")
    content = (f"<job>\n{job.get('title')} at {job.get('company')} ({job.get('location') or 'location n/a'})\n"
               f"{(job.get('description') or '')[:6000]}\n</job>\n\n"
               f"Today's date: {date.today().isoformat()}\n\n<fields>\n" + "\n".join(lines) + "\n</fields>\n\n"
               "Return one entry per field id.")
    # Thinking is billed as output: only forms with written answers get the deeper pass.
    effort = "medium" if any(is_open_ended(f) for f in fields) else "low"
    data = await llm.ajson_call(ANSWER_SYSTEM, content, ANSWER_SCHEMA, effort=effort, model=llm.model_for("answers"),
                                context=context, purpose="answers")
    return {a["id"]: a for a in data["answers"]}


def is_open_ended(f: dict) -> bool:
    q = (f.get("question") or "").lower()
    return f["kind"] == "textarea" or bool(re.search(r"\bwhy\b|describe|tell us|explain|what (excites|interests|motivates)|share (a|an|your)", q))


# ---------------- cover letter ----------------

COVER_SCHEMA = obj({"body": S})
COVER_SYSTEM = """Write a cover letter body for the candidate and job below. 180-260 words, 3-4 short paragraphs, first person.
Open with who the candidate is and why this specific role at this company (name something concrete from the posting).
Then one or two projects or roles from the profile that fit the posting best, told briefly; pick, don't list everything, and
don't restate resume bullets word for word or read the posting's own bullets back to it.
Only use facts from the profile. No embellishment: no motivations, processes, users, feedback loops, adjectives about past
systems, lessons learned or authorship the profile doesn't state, and never recast what a project does to fit the posting.
Use absolute dates ("in summer 2025"), never "last summer"; today's date is given.
Plain, confident, specific. No clichés ("I am writing to express"), no em dashes, no exclamation marks, no placeholders.
Do not include a greeting line, date, address block, or signature; just the paragraphs separated by blank lines."""


async def cover_letter(p: dict, job: dict) -> str:
    content = (f"<job>\n{job.get('title')} at {job.get('company')} ({job.get('location') or 'location n/a'})\n"
               f"{(job.get('description') or '')[:6000]}\n</job>\n\nToday's date: {date.today().isoformat()}")
    data = await llm.ajson_call(COVER_SYSTEM, content, COVER_SCHEMA, effort="medium", model=llm.model_for("writing"),
                                context=f"<candidate_profile>\n{_profile_text(p)}\n</candidate_profile>", purpose="cover_letter")
    return data["body"].strip()


def cover_letter_html(p: dict, job: dict, body: str) -> str:
    import html as h
    c = p["contact"]
    name = f"{c['first_name']} {c['last_name']}".strip()
    contact = " · ".join(x for x in [c.get("email"), c.get("phone"), ", ".join(y for y in [c.get('city'), c.get('state')] if y),
                                      c.get("linkedin")] if x)
    paras = "".join(f"<p>{h.escape(para.strip())}</p>" for para in body.split("\n\n") if para.strip())
    return f"""<html><head><meta charset="utf-8"><style>
    body {{ font-family: Georgia, 'Times New Roman', serif; font-size: 11.5pt; line-height: 1.5; color: #111; margin: 0; }}
    .wrap {{ padding: 0.9in 1in; }} h1 {{ font-size: 18pt; margin: 0 0 2pt; font-weight: normal; }}
    .contact {{ font-size: 9.5pt; color: #444; margin-bottom: 26pt; }} p {{ margin: 0 0 11pt; }}
    </style></head><body><div class="wrap"><h1>{h.escape(name)}</h1><div class="contact">{h.escape(contact)}</div>
    <p>{date.today().strftime('%B %d, %Y').replace(' 0', ' ')}</p>
    <p>Dear {h.escape(job.get('company') or '')} Hiring Team,</p>{paras}
    <p>Sincerely,<br>{h.escape(name)}</p></div></body></html>"""


def cover_letter_path(app_id: int, p: dict) -> Path:
    from ..config import FILES
    c = p["contact"]
    name = "_".join(x for x in [c.get("first_name"), c.get("last_name")] if x) or "Cover"
    d = FILES / str(app_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}_Cover_Letter.pdf"
