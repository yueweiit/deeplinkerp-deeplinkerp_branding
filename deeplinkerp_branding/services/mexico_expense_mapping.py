"""Company alias suggestions only; never used to assign financial records."""
import re
import unicodedata
from difflib import SequenceMatcher


def suggestion_key(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s/()（）·_-]+", "", text)


def suggest_companies(label, kind, companies, mappings):
    token = suggestion_key(label)
    if not token:
        return []
    allowed = set(companies)
    # A department is not a legal entity: only explicit department aliases can
    # provide suggestions for it. Final assignment always needs confirmation.
    aliases = [(company, company) for company in companies] if kind == "entity" else []
    aliases += [(row["source_entity"], row["company"]) for row in mappings
                if row["entity_kind"] == kind and row["company"] in allowed]
    scores = {}
    for alias, company in aliases:
        other = suggestion_key(alias)
        exact = token == other
        score = 1.0 if exact else SequenceMatcher(None, token, other).ratio()
        if exact or (min(len(token), len(other)) >= 4 and score >= 0.72):
            scores[company] = max(scores.get(company, 0), score)
    return [{"company": company, "match": "normalized" if score == 1 else "similar"}
            for company, score in sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))[:3]]
