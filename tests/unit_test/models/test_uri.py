import pytest

from ontyra.models.uri import canonical_uri, uri_ancestors


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("file:///home/x/proj/", "file://m1/home/x/proj"),
        ("FILE://M1/home//x/./proj/../proj", "file://m1/home/x/proj"),
        ("ssh://someone@Somewhere.COM/", "ssh://someone@somewhere.com"),
        ("git+https://GitHub.com/org/repo.git", "git+https://github.com/org/repo"),
        ("https://example.com/a/b/#frag", "https://example.com/a/b"),
        ("host:Somewhere.com", "host:somewhere.com"),
        ("harness:claude-code", "harness:claude-code"),
        ("ontyra:project//yyy/", "ontyra:project/yyy"),
    ],
)
def test_canonical_form(raw: str, expected: str) -> None:
    assert canonical_uri(raw, machine="M1") == expected


@pytest.mark.parametrize("bad", ["", "   ", "nonsense", "host:", "https:///nohost"])
def test_rejects_non_uris(bad: str) -> None:
    with pytest.raises(ValueError):
        canonical_uri(bad)


def test_file_ancestors_are_nearest_first() -> None:
    assert uri_ancestors("file://m1/home/x/proj/src/a.py") == [
        "file://m1/home/x/proj/src/a.py",
        "file://m1/home/x/proj/src",
        "file://m1/home/x/proj",
        "file://m1/home/x",
        "file://m1/home",
        "file://m1",
    ]


def test_ssh_account_is_contained_in_its_host() -> None:
    chain = uri_ancestors("ssh://someone@somewhere.com/data/applications")
    assert chain[1] == "ssh://someone@somewhere.com/data"
    assert chain[-2:] == ["ssh://someone@somewhere.com", "host:somewhere.com"]


def test_opaque_schemes_nest_by_slash() -> None:
    assert uri_ancestors("ontyra:project/yyy/sub") == [
        "ontyra:project/yyy/sub",
        "ontyra:project/yyy",
        "ontyra:project",
    ]


def test_sibling_paths_do_not_share_a_chain_entry() -> None:
    mine = set(uri_ancestors("file://m1/home/x/proj/a.py"))
    assert "file://m1/home/x/other" not in mine
    assert "file://m1/home/x/proj" in mine
