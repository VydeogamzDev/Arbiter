import pytest

from semver import compare, sort_versions


def test_numeric_core():
    assert compare("1.10.0", "1.9.0") == 1 and compare("2.0.0", "10.0.0") == -1
    assert compare("1.2.3", "1.2.3") == 0 and compare("1.2.10", "1.2.9") == 1


def test_prerelease_lower():
    assert compare("1.0.0-alpha", "1.0.0") == -1 and compare("1.0.0", "1.0.0-rc.1") == 1


def test_prerelease_chain():
    chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2",
             "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
    for lo, hi in zip(chain, chain[1:]):
        assert compare(lo, hi) == -1, (lo, hi)
        assert compare(hi, lo) == 1, (hi, lo)
    assert compare("1.0.0-2", "1.0.0-10") == -1
    assert compare("1.0.0-9", "1.0.0-a") == -1


def test_build_ignored():
    assert compare("1.0.0+build.1", "1.0.0+build.2") == 0
    assert compare("1.0.0-rc.1+x", "1.0.0-rc.1") == 0


@pytest.mark.parametrize("bad", ["1.2", "1.2.3.4", "01.2.3", "1.02.3", "1.2.x", "", "v1.2.3", "1.2.3-"])
def test_invalid_core(bad):
    with pytest.raises(ValueError):
        compare(bad, "1.0.0")


@pytest.mark.parametrize("bad", ["1.2.3-01", "1.2.3-alpha..1", "1.2.3-alpha.", "1.2.3+", "1.2.3-a$b"])
def test_invalid_prerelease(bad):
    with pytest.raises(ValueError):
        compare("1.0.0", bad)


def test_sort_new_list():
    vs = ["1.0.0", "1.0.0-beta", "0.9.12", "1.0.0-alpha.1", "1.0.0-alpha"]
    before = list(vs)
    out = sort_versions(vs)
    assert out == ["0.9.12", "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-beta", "1.0.0"]
    assert vs == before and out is not vs
