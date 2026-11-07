"""check_new_emails.py: the cheap mailbox probe behind the weekday morning
watcher. Gmail API only (no browser); exit 0 = there is a Dispatch email we
have not converted yet, 1 = nothing new, 2 = could not check."""
from unittest.mock import MagicMock, patch

LINK = "https://l.thedispatch.com/z/c/27175/abc/def"


def _handler(data_by_id, url_by_subject):
    h = MagicMock()
    h.search_dispatch_emails.return_value = [{"id": k} for k in data_by_id]
    h.get_message_content.side_effect = lambda mid: {"id": mid}
    h.extract_email_data.side_effect = lambda msg: data_by_id[msg["id"]]
    h.extract_read_online_url.side_effect = lambda d: url_by_subject.get(d["subject"])
    return h


def test_find_unprocessed_returns_only_new_emails_that_have_a_read_online_link():
    from check_new_emails import find_unprocessed
    data = {
        "1": {"subject": "Late Morning Dispatch", "message_id": "1"},
        "2": {"subject": "Already converted", "message_id": "2"},
        "3": {"subject": "Click Here to Log in to The Dispatch", "message_id": "3"},
    }
    handler = _handler(data, {"Late Morning Dispatch": LINK, "Already converted": LINK})
    tracking = MagicMock()
    tracking.is_email_processed.side_effect = lambda d: d["subject"] == "Already converted"

    assert [d["subject"] for d in find_unprocessed(handler, tracking)] == ["Late Morning Dispatch"]


def test_find_unprocessed_is_none_when_the_search_itself_failed():
    from check_new_emails import find_unprocessed
    handler = MagicMock()
    handler.search_dispatch_emails.return_value = None
    assert find_unprocessed(handler, MagicMock()) is None


def _run_main(monkeypatch, found, google_ok=True):
    import check_new_emails
    with patch("check_new_emails.AuthManager") as A, patch("check_new_emails.EmailHandler"), \
         patch("check_new_emails.TrackingManager"):
        A.return_value.authenticate_google.return_value = google_ok
        monkeypatch.setattr(check_new_emails, "find_unprocessed", lambda h, t: found)
        return check_new_emails.main()


def test_main_exits_0_when_there_is_new_mail(monkeypatch, capsys):
    assert _run_main(monkeypatch, [{"subject": "Late Morning Dispatch"}]) == 0
    assert "Late Morning Dispatch" in capsys.readouterr().out


def test_main_exits_1_when_nothing_is_new(monkeypatch):
    assert _run_main(monkeypatch, []) == 1


def test_main_exits_2_when_it_could_not_check(monkeypatch):
    assert _run_main(monkeypatch, None) == 2
    assert _run_main(monkeypatch, [{"subject": "x"}], google_ok=False) == 2
