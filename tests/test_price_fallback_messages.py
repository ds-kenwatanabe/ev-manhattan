from src.run.web_app import _nyiso_error_summary


def test_nyiso_dns_errors_are_summarized_for_ui():
    errors = [
        "day-ahead: <urlopen error [Errno -3] Temporary failure in name resolution>",
        "real-time: <urlopen error [Errno -3] Temporary failure in name resolution>",
    ]

    assert _nyiso_error_summary(errors) == "network/DNS unavailable"


def test_nyiso_not_found_errors_are_summarized_for_ui():
    errors = ["day-ahead: HTTP Error 404: Not Found"]

    assert _nyiso_error_summary(errors) == "NYISO data not published for that date"
