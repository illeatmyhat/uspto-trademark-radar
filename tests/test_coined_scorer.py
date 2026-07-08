from uspto_trademark_radar.gold.coined_scorer import CoinedScorer

# A small "ordinary English" reference vocabulary.
_REFERENCE = """
apple orange banana table chair window garden flower morning evening
water river mountain forest bridge castle silver golden simple gentle
market bright shadow thunder harvest wonder journey lantern harbor
premium natural organic classic modern comfort quality wedding titan
""".split() * 20


def _scorer():
    return CoinedScorer(order=3).fit(_REFERENCE)


def _score(s: CoinedScorer, word: str) -> float:
    v = s.score(word)
    assert v is not None, word
    return v


def test_coined_marks_score_higher_than_ordinary():
    s = _scorer()
    coined = ["QVOWII", "BRCKD", "KOUZUO", "VOFULY", "ZXQWJ"]
    ordinary = ["APPLE", "SILVER", "MARKET", "WEDDING", "TITAN"]
    worst_ordinary = max(_score(s, w) for w in ordinary)
    best_coined = min(_score(s, w) for w in coined)
    assert best_coined > worst_ordinary, (best_coined, worst_ordinary)


def test_score_bounds_and_rounding():
    s = _scorer()
    for w in ["QVOWII", "APPLE", "zzzzz", "garden"]:
        v = _score(s, w)
        assert 0.0 <= v <= 1.0


def test_unscoreable_inputs_return_none():
    s = _scorer()
    assert s.score(None) is None
    assert s.score("") is None
    assert s.score("A") is None      # single letter
    assert s.score("7") is None      # no alpha


def test_unfit_scorer_returns_none():
    assert CoinedScorer().score("APPLE") is None
