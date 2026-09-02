from __future__ import annotations

from braiaudit.relevance import answer_completeness, cosine_similarity, rank_candidates


def test_identical_text_scores_high_similarity():
    text = "our enterprise plan costs 49 dollars per seat per month billed annually"
    assert cosine_similarity(text, text) > 0.99


def test_unrelated_text_scores_low_similarity():
    query = "what does this product cost"
    unrelated = "we are hiring backend engineers to join our growing careers team"
    assert cosine_similarity(query, unrelated) < 0.2


def test_answer_completeness_rewards_direct_coverage():
    query = "what does this product cost"
    on_topic = "Our pricing starts at $49/month per seat for the product's core plan."
    off_topic = "We are hiring across engineering, sales, and design roles this quarter."
    assert answer_completeness(query, on_topic) > answer_completeness(query, off_topic)


def test_rank_candidates_orders_by_relevance_descending():
    query = "how do I contact support"
    candidates = {
        "/careers": "join our team open roles engineering sales",
        "/support": "contact support email phone live chat help desk",
        "/pricing": "plans and pricing tiers billed monthly",
    }
    ranked = rank_candidates(query, candidates)
    assert ranked[0][0] == "/support"
    assert ranked[0][1] >= ranked[1][1] >= ranked[2][1]


def test_empty_text_yields_zero_score():
    assert cosine_similarity("anything", "") == 0.0
    assert answer_completeness("anything", "") == 0.0
