import pytest

from uspto_trademark_radar.normalize import canonical_attorney as c

# All names below are fictional placeholders used only to exercise the
# spelling/order/suffix/middle-name normalization logic.


@pytest.mark.parametrize("variants,expected", [
    (["Dana K. Rivers", "DANA K. RIVERS", "Dana Rivers",
      "Dana K. Rivers, Esq.", "Dana  Rivers"], "DANA RIVERS"),
    (["Alex T. Warren", "Alex Thomas Warren", "ALEX WARREN"], "ALEX WARREN"),
    (["KANG, MIN", "Min Kang", "MIN  KANG"], "KANG MIN"),
    (["Jamie Fox", "Jamie  Fox"], "FOX JAMIE"),
    (["Ann-Marie Lowe"], "ANN-MARIE LOWE"),
])
def test_variants_collapse(variants, expected):
    keys = {c(v) for v in variants}
    assert keys == {expected}, keys


def test_suffixes_and_punct_stripped():
    assert c("Morgan P. Reed, PLLC") == "MORGAN REED"
    assert c("Pat Steele, Esq.") == "PAT STEELE"


def test_degenerate_inputs():
    assert c(None) is None
    assert c("") is None
    assert c("   ") is None
    assert c(",.") is None
    assert c("Cher") is None            # single-token: not reliably resolvable


def test_docket_code_noise_dropped():
    # docket refs sometimes land in the attorney field; must not become keys
    assert c("QO2281") is None
    assert c("QO1577") is None


def test_multi_attorney_field_takes_first_name():
    # "A and B" must not produce a chimeric key mixing two people
    assert c("Sam A. Cole and Robin B. Vance") == "COLE SAM"
    assert c("Pat Steele & Dana Rivers") == "PAT STEELE"


def test_order_invariance():
    assert c("Min Kang") == c("Kang Min") == c("KANG, MIN")


def test_distinct_people_stay_distinct():
    assert c("Dana Rivers") != c("Morgan Reed")


def test_known_over_merge_limitation():
    # documented ceiling: different middle initials collapse (accepted risk)
    assert c("Chris T. Baker") == c("Chris N. Baker") == "BAKER CHRIS"
