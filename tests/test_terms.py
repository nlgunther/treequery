import pytest

from treequery.terms import MAX_COUNTED_HITS, NAME_HIT_BONUS, build_groups, score

SPEC = {
    "contractor": {"weight": 5, "any": ["Morgan", "New Season"]},
    "money": {"weight": 2, "regex": r"\$\s?\d{1,3}(,\d{3})*(\.\d\d)?", "capture": True},
}


def test_words_match_whole_words_case_insensitively():
    group = build_groups(SPEC)["contractor"]
    assert group.matches("paid MORGAN today") and group.matches("the new season crew")
    assert not group.matches("Morganstern")           # not a whole word


def test_score_counts_text_hits_caps_them_and_rewards_name_hits():
    groups = build_groups(SPEC)
    plain = score(groups, "notes.txt", "Morgan Morgan")
    assert plain.total == 5 * 2 and plain.hits == {"contractor": 2}
    flood = score(groups, "notes.txt", "Morgan " * 50)
    assert flood.total == 5 * MAX_COUNTED_HITS
    named = score(groups, "Morgan.pdf", "Morgan")
    assert named.total == 5 * (1 + NAME_HIT_BONUS)


def test_capture_keeps_matched_amounts():
    card = score(build_groups(SPEC), "x.txt", "paid $4,500.00 then $12,000")
    assert card.captured == ("$4,500.00", "$12,000")
    assert card.total == 2 * 2


def test_no_hits_scores_zero():
    assert score(build_groups(SPEC), "x.txt", "nothing").total == 0


@pytest.mark.parametrize("bad, message", [
    ({"g": "words"}, "mapping"),
    ({"g": {"weight": 1}}, "needs"),
    ({"g": {"any": ["x"], "wieght": 2}}, "unknown keys"),
])
def test_malformed_groups_raise(bad, message):
    with pytest.raises(ValueError, match=message):
        build_groups(bad)
