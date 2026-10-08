"""Query-aware candidate fusion; ranking never widens the authorized corpus.

RRF combines ranks, not incomparable BM25/cosine scores. A lexical match is
especially useful for identifiers, whereas semantic questions favour E5.
The conservative automatic route avoids unconditionally replacing this order
with an uncalibrated pointwise CrossEncoder score.
"""
import re


def identifier_terms(query):
    """Recognize explicit mixed letter/number identifiers, not ordinary words."""
    return list(dict.fromkeys(re.findall(
        r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[-._][A-Za-z0-9]+)*(?![A-Za-z0-9])",
        query.lower()))) if re.search(r"[A-Za-z]", query) else []


def rank_candidates(query, keyword, dense, lookup, *, automatic=False):
    identifiers = [term for term in identifier_terms(query) if any(c.isdigit() for c in term)]
    route = 'identifier' if automatic and identifiers else 'semantic' if automatic else 'equal_rrf'
    weights = (1.0, 1.0) if not automatic else (1.0, 4.0)
    scores = {}
    for weight, ranked in zip(weights, (keyword, dense)):
        for position, cid in enumerate(ranked, 1):
            scores[cid] = scores.get(cid, 0.0) + weight / (60 + position)

    def exact(cid):
        # Tokenizer decoding may insert spaces around punctuation. Verify the
        # complete identifier; "HT-20" must not match "HT-204".
        content = re.sub(r'\s+', '', lookup[cid].get('content', '').lower())
        return bool(identifiers) and all(re.search(
            r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', content) for term in identifiers)

    dense_positions = {cid: i for i, cid in enumerate(dense)}
    keyword_positions = {cid: i for i, cid in enumerate(keyword)}
    ranked = sorted(scores, key=lambda cid: (
        -int(route == 'identifier' and exact(cid)), -scores[cid],
        dense_positions.get(cid, len(dense)), keyword_positions.get(cid, len(keyword)), cid))
    # Rank agreement is not calibrated relevance. Preserve a small semantic
    # head, then use fusion to backfill complementary sources. This prevents
    # incidental lexical overlap from displacing the strongest E5 evidence.
    anchors = dense[:3] if automatic and route == 'semantic' else []
    if anchors:
        ranked = anchors + [cid for cid in ranked if cid not in anchors]
    return ranked, {'route': route, 'weights': {'keyword': weights[0], 'vector': weights[1]},
                    'rrf_constant': 60, 'identifiers': identifiers,
                    'protected_dense_head': anchors,
                    'scores': scores, 'candidate_count': len(scores),
                    'rerank_applied': False}
