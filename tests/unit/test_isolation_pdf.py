"""The source PDF of each account: every byte, stream and pixel (docs/auth-rbac.md §4, §5; INV-8)."""

from __future__ import annotations

from collections.abc import Iterator

import pymupdf
import pytest

from auth_helpers import FULL, LIMITED, PDF, Accounts, running_app, settings_in
from autorfx.rules import Ruleset
from isolation_package import (
    ACTUAL_TEXT,
    ATTACHED,
    BOOKMARK,
    FILES,
    GHOLT,
    NOTE,
    OFF_PAGE,
    OWNERS,
    make_isolation_package,
)
from scoped_pdf_helpers import assert_flat, box_of, has_ink, is_grey, is_red, pixels, words_in

Api = tuple[Accounts, str]
PAIRS = [(name, file.id) for name in LIMITED for file in FILES]


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory, ruleset: Ruleset) -> Iterator[Api]:
    root = tmp_path_factory.mktemp("isolation-pdf")
    with running_app(settings_in(root), ruleset) as accounts:
        yield accounts, accounts.upload(make_isolation_package(root / "in"))


def source(accounts: Accounts, rfq_id: str, name: str, file_id: str, **headers: str):  # type: ignore[no-untyped-def]
    return accounts[name].get(f"/rfqs/{rfq_id}/files/{file_id}/source", headers=headers)


def rects(accounts: Accounts, rfq_id: str, name: str, file_id: str) -> dict[int, list[dict]]:
    """Sentence rectangles of page 1 by sentence number, as this account gets them."""
    path = f"/rfqs/{rfq_id}/files/{file_id}/pages/1/sentences"
    found = accounts[name].get(path).json()["data"]["sentences"]
    return {s["locus"]["sentence_index"]: s["rects"] for s in found}


def test_the_files_hold_what_these_tests_look_for(api: Api):
    """Precondition: the full scope gets the canonical file, and everything to be hidden is in it."""
    accounts, rfq_id = api
    for name in FULL:
        main = source(accounts, rfq_id, name, "d01").content
        assert {BOOKMARK, NOTE, ATTACHED, GHOLT} <= words_in(main, OWNERS)
        extra = source(accounts, rfq_id, name, "d02").content
        assert {OFF_PAGE, ACTUAL_TEXT} <= words_in(extra, OWNERS)
        with pymupdf.open(stream=extra, filetype="pdf") as doc:
            assert sum(is_red(p) for p in pixels(doc[0])) > 500, "the picture"
            assert OFF_PAGE not in doc[0].get_text(), "outside the crop box"
        canonical = accounts.settings.work_dir / rfq_id / "canonical" / "d02.pdf"
        assert extra == canonical.read_bytes()


@pytest.mark.parametrize(("name", "file_id"), PAIRS)
def test_a_scoped_pdf_has_no_word_of_the_package_in_it(api: Api, name: str, file_id: str):
    """auth-rbac §5: raw bytes, every object and every decompressed stream: no sentinel at all, not even
    the account's own: the page is a picture."""
    accounts, rfq_id = api
    response = source(accounts, rfq_id, name, file_id)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == PDF and response.headers["cache-control"] == "no-store"
    assert words_in(response.content, OWNERS) == set()
    assert not [w for w in OWNERS if w in str(response.headers)]
    assert_flat(response.content)


@pytest.mark.parametrize(("name", "file_id"), PAIRS)
def test_own_sentences_are_drawn_and_everything_else_is_grey(api: Api, name: str, file_id: str):
    """auth-rbac §5.5-6: ink at the account's own boxes; every box of another sentence, the picture and
    the rest of the page are grey."""
    accounts, rfq_id = api
    content = source(accounts, rfq_id, name, file_id).content
    mine = rects(accounts, rfq_id, name, file_id)
    everyone = rects(accounts, rfq_id, "admin", file_id)
    assert set(mine) <= set(everyone)
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        canonical = source(accounts, rfq_id, "admin", file_id).content
        with pymupdf.open(stream=canonical, filetype="pdf") as original:
            assert [p.rect for p in doc] == [p.rect for p in original]
            page = doc[0]
            for number, boxes in everyone.items():
                for rect in boxes:
                    if number in mine:
                        assert has_ink(page, rect), (name, file_id, number)
                        assert has_ink(original[0], rect)
                    else:
                        found = pixels(page, box_of(page, rect, trim=1.0))
                        assert all(is_grey(p) for p in found), (name, file_id, number)
            assert not any(is_red(p) for p in pixels(page)), "the picture is in no sentence box"
            own_area = sum(box_of(page, r).get_area() for boxes in mine.values() for r in boxes)
            all_pixels = pixels(page)
            grey = sum(is_grey(p) for p in all_pixels) / len(all_pixels)
            assert grey >= 1 - 1.3 * own_area / page.rect.get_area() - 0.02


def test_an_account_gets_its_own_pdf(api: Api):
    """Different scopes get different files; the same scope gets the same file."""
    accounts, rfq_id = api
    found = {name: source(accounts, rfq_id, name, "d02").content for name in LIMITED}
    assert len(set(found.values())) == len(found), "each department owns something else in this file"
    again = source(accounts, rfq_id, "quality", "d02").content
    assert again == found["quality"]


@pytest.mark.parametrize("name", LIMITED)
def test_conditional_and_partial_requests_stay_in_the_scope(api: Api, name: str):
    """Security: an ETag or a byte range taken from another account's answer opens nothing."""
    accounts, rfq_id = api
    full = source(accounts, rfq_id, "sales", "d01")
    mine = source(accounts, rfq_id, name, "d01")
    assert mine.content != full.content
    tag, modified = full.headers.get("etag"), full.headers.get("last-modified")
    if tag:
        assert mine.headers.get("etag") != tag
    for headers in (
        {"If-None-Match": tag or '"x"'},
        {"If-None-Match": "*"},
        {"If-Modified-Since": modified or "Wed, 01 Jan 2031 00:00:00 GMT"},
        {"Range": "bytes=0-4095"},
        {"Range": "bytes=-4096"},
        {"Range": "bytes=0-4095", "If-Range": tag or '"x"'},
        {"Range": "bytes=0-9,20-29"},
    ):
        response = source(accounts, rfq_id, name, "d01", **headers)
        assert response.status_code in (200, 206, 304, 412, 416), headers
        body = response.content
        if response.status_code == 200:
            assert body == mine.content, headers
        elif response.status_code == 206 and "," not in headers["Range"]:
            assert body in mine.content, headers
        assert not [w for w in OWNERS if w.encode() in body]


def test_the_exception_of_pre_cost_does_not_open_the_pdf(api: Api):
    """auth-rbac §2: purchasing and costing compute with the whole RFQ, but get the scoped PDF."""
    accounts, rfq_id = api
    canonical = source(accounts, rfq_id, "admin", "d02").content
    for name in ("purchasing", "costing"):
        assert source(accounts, rfq_id, name, "d02").content != canonical


def test_a_department_change_changes_the_pdf_at_once(tmp_path, ruleset: Ruleset):
    """auth-rbac §3: the cached picture belongs to the scope, not to the account."""
    with running_app(settings_in(tmp_path), ruleset, names=("admin", "sales", "legal")) as accounts:
        rfq_id = accounts.upload(make_isolation_package(tmp_path / "in"))
        before = source(accounts, rfq_id, "legal", "d02").content
        users = accounts["admin"].get("/admin/users").json()["data"]["users"]
        user_id = next(u["id"] for u in users if u["username"] == "legal")
        changed = accounts["admin"].patch(f"/admin/users/{user_id}", json={"department": "logistics"})
        assert changed.status_code == 200
        after = source(accounts, rfq_id, "legal", "d02").content
        assert after != before and words_in(after, OWNERS) == set()


def test_no_other_route_serves_a_file_of_the_workspace(api: Api):
    """The stored originals, canonical and masked copies and the scoped cache have no address."""
    accounts, rfq_id = api
    source(accounts, rfq_id, "quality", "d01")
    for path in (
        f"/rfqs/{rfq_id}/files/d01.masked/source",
        f"/rfqs/{rfq_id}/files/..%2Fcanonical%2Fd01/source",
        f"/rfqs/{rfq_id}/scoped/x.pdf",
        f"/rfqs/{rfq_id}/canonical/d01.pdf",
        f"/rfqs/{rfq_id}/manifest.json",
    ):
        assert accounts["quality"].get(path).status_code == 404, path
