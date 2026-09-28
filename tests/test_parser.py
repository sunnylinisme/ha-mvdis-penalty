"""Tests for the MVDIS HTML parser and TLS configuration."""

import ssl

import pytest
from mvdis import CaptchaError, mvdis_ssl_context, parse_response


def test_mvdis_tls_keeps_verification_without_strict_mode() -> None:
    context = mvdis_ssl_context()
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert not context.verify_flags & ssl.VERIFY_X509_STRICT


def test_parse_empty_result() -> None:
    data = parse_response("<html><body>查無交通違規資料</body></html>")
    assert len(data.penalties) == 0


def test_parse_captcha_error_in_javascript() -> None:
    html = """
    <html><body>
      <span id="validateStr1"></span>
      <script>$('#validateStr1').text('驗證碼輸入錯誤');</script>
    </body></html>
    """

    with pytest.raises(CaptchaError, match="rejected the CAPTCHA"):
        parse_response(html)


def test_parse_result_table() -> None:
    html = """
    <html><body>
      <table>
        <tr><th>違規日期</th><th>違規事實</th><th>違規地點</th><th>應繳金額</th></tr>
        <tr>
          <td>115/01/02</td><td>超速</td><td>測試路段</td>
          <td>新臺幣 1,200 元</td>
        </tr>
        <tr><td>115/02/03</td><td>違規停車</td><td>範例路口</td><td>900</td></tr>
      </table>
    </body></html>
    """
    data = parse_response(html)
    assert len(data.penalties) == 2
    assert sum(item.amount or 0 for item in data.penalties) == 2100
    assert data.penalties[0].key != data.penalties[1].key
    assert "超速" in data.penalties[0].summary
    assert data.penalties[0].details == {
        "違規日期": "115/01/02",
        "違規事實": "超速",
        "違規地點": "測試路段",
        "應繳金額": "新臺幣 1,200 元",
    }


def test_duplicate_tables_are_deduplicated() -> None:
    table = """
      <table>
        <tr><th>違規日</th><th>違規事實</th><th>罰鍰金額</th></tr>
        <tr><td>115/01/02</td><td>測試</td><td>600</td></tr>
      </table>
    """
    data = parse_response(f"<html><body>{table}{table}</body></html>")
    assert len(data.penalties) == 1
    assert sum(item.amount or 0 for item in data.penalties) == 600


def test_due_date_is_not_misread_as_money() -> None:
    html = """
      <table>
        <tr><th>違規日</th><th>違規事實</th><th>應繳日期</th></tr>
        <tr><td>115/01/02</td><td>測試</td><td>115/02/02</td></tr>
      </table>
    """
    data = parse_response(html)
    assert len(data.penalties) == 1
    assert data.penalties[0].amount is None
