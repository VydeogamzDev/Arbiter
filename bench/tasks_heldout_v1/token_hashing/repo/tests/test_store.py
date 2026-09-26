from auth.store import TokenStore


def test_issue_verify():
    s = TokenStore()
    t = s.issue("ann")
    assert s.verify("ann", t)
