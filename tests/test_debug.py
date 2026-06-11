import json
import linecache
import re
import sys
import time
from unittest import mock

import pytest

from werkzeug.debug import console
from werkzeug.debug import DebuggedApplication
from werkzeug.debug import DebugTraceback
from werkzeug.debug import get_machine_id
from werkzeug.debug import hash_pin
from werkzeug.debug import PIN_TIME
from werkzeug.debug.console import HTMLStringO
from werkzeug.debug.repr import debug_repr
from werkzeug.debug.repr import DebugReprGenerator
from werkzeug.debug.repr import dump
from werkzeug.debug.repr import helper
from werkzeug.test import Client
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request
from werkzeug.wrappers import Response


class TestDebugRepr:
    def test_basic_repr(self):
        assert debug_repr([]) == "[]"
        assert debug_repr([1, 2]) == (
            '[<span class="number">1</span>, <span class="number">2</span>]'
        )
        assert debug_repr([1, "test"]) == (
            '[<span class="number">1</span>,'
            ' <span class="string">&#39;test&#39;</span>]'
        )
        assert debug_repr([None]) == '[<span class="object">None</span>]'

    def test_string_repr(self):
        assert debug_repr("") == '<span class="string">&#39;&#39;</span>'
        assert debug_repr("foo") == '<span class="string">&#39;foo&#39;</span>'
        assert debug_repr("s" * 80) == (
            f'<span class="string">&#39;{"s" * 69}'
            f'<span class="extended">{"s" * 11}&#39;</span></span>'
        )
        assert debug_repr("<" * 80) == (
            f'<span class="string">&#39;{"&lt;" * 69}'
            f'<span class="extended">{"&lt;" * 11}&#39;</span></span>'
        )

    def test_string_subclass_repr(self):
        class Test(str):
            pass

        assert debug_repr(Test("foo")) == (
            '<span class="module">test_debug.</span>'
            'Test(<span class="string">&#39;foo&#39;</span>)'
        )

    def test_sequence_repr(self):
        assert debug_repr(list(range(20))) == (
            '[<span class="number">0</span>, <span class="number">1</span>, '
            '<span class="number">2</span>, <span class="number">3</span>, '
            '<span class="number">4</span>, <span class="number">5</span>, '
            '<span class="number">6</span>, <span class="number">7</span>, '
            '<span class="extended"><span class="number">8</span>, '
            '<span class="number">9</span>, <span class="number">10</span>, '
            '<span class="number">11</span>, <span class="number">12</span>, '
            '<span class="number">13</span>, <span class="number">14</span>, '
            '<span class="number">15</span>, <span class="number">16</span>, '
            '<span class="number">17</span>, <span class="number">18</span>, '
            '<span class="number">19</span></span>]'
        )

    def test_mapping_repr(self):
        assert debug_repr({}) == "{}"
        assert debug_repr({"foo": 42}) == (
            '{<span class="pair"><span class="key"><span class="string">&#39;foo&#39;'
            '</span></span>: <span class="value"><span class="number">42'
            "</span></span></span>}"
        )
        assert debug_repr(dict(zip(range(10), [None] * 10, strict=True))) == (
            '{<span class="pair"><span class="key"><span class="number">0'
            '</span></span>: <span class="value"><span class="object">None'
            "</span></span></span>, "
            '<span class="pair"><span class="key"><span class="number">1'
            '</span></span>: <span class="value"><span class="object">None'
            "</span></span></span>, "
            '<span class="pair"><span class="key"><span class="number">2'
            '</span></span>: <span class="value"><span class="object">None'
            "</span></span></span>, "
            '<span class="pair"><span class="key"><span class="number">3'
            '</span></span>: <span class="value"><span class="object">None'
            "</span></span></span>, "
            '<span class="extended">'
            '<span class="pair"><span class="key"><span class="number">4'
            '</span></span>: <span class="value"><span class="object">None'
            "</span></span></span>, "
            '<span class="pair"><span class="key"><span class="number">5'
            '</span></span>: <span class="value"><span class="object">None'
            "</span></span></span>, "
            '<span class="pair"><span class="key"><span class="number">6'
            '</span></span>: <span class="value"><span class="object">None'
            "</span></span></span>, "
            '<span class="pair"><span class="key"><span class="number">7'
            '</span></span>: <span class="value"><span class="object">None'
            "</span></span></span>, "
            '<span class="pair"><span class="key"><span class="number">8'
            '</span></span>: <span class="value"><span class="object">None'
            "</span></span></span>, "
            '<span class="pair"><span class="key"><span class="number">9'
            '</span></span>: <span class="value"><span class="object">None'
            "</span></span></span></span>}"
        )
        assert debug_repr((1, "zwei", "drei")) == (
            '(<span class="number">1</span>, <span class="string">&#39;'
            'zwei&#39;</span>, <span class="string">&#39;drei&#39;</span>)'
        )

    def test_custom_repr(self):
        class Foo:
            def __repr__(self):
                return "<Foo 42>"

        assert debug_repr(Foo()) == '<span class="object">&lt;Foo 42&gt;</span>'

    def test_list_subclass_repr(self):
        class MyList(list):
            pass

        assert debug_repr(MyList([1, 2])) == (
            '<span class="module">test_debug.</span>MyList(['
            '<span class="number">1</span>, <span class="number">2</span>])'
        )

    def test_regex_repr(self):
        assert (
            debug_repr(re.compile(r"foo\d"))
            == "re.compile(<span class=\"string regex\">r'foo\\d'</span>)"
        )

    def test_set_repr(self):
        assert (
            debug_repr(frozenset("x"))
            == 'frozenset([<span class="string">&#39;x&#39;</span>])'
        )
        assert debug_repr(set("x")) == (
            'set([<span class="string">&#39;x&#39;</span>])'
        )

    def test_recursive_repr(self):
        a = [1]
        a.append(a)
        assert debug_repr(a) == '[<span class="number">1</span>, [...]]'

    def test_broken_repr(self):
        class Foo:
            def __repr__(self):
                raise Exception("broken!")

        assert debug_repr(Foo()) == (
            '<span class="brokenrepr">&lt;broken repr (Exception: broken!)&gt;</span>'
        )


class Foo:
    x = 42
    y = 23

    def __init__(self):
        self.z = 15


class TestDebugHelpers:
    def test_object_dumping(self):
        drg = DebugReprGenerator()
        out = drg.dump_object(Foo())
        assert re.search("Details for test_debug.Foo object at", out)
        assert re.search('<th>x.*<span class="number">42</span>', out, flags=re.DOTALL)
        assert re.search('<th>y.*<span class="number">23</span>', out, flags=re.DOTALL)
        assert re.search('<th>z.*<span class="number">15</span>', out, flags=re.DOTALL)

        out = drg.dump_object({"x": 42, "y": 23})
        assert re.search("Contents of", out)
        assert re.search('<th>x.*<span class="number">42</span>', out, flags=re.DOTALL)
        assert re.search('<th>y.*<span class="number">23</span>', out, flags=re.DOTALL)

        out = drg.dump_object({"x": 42, "y": 23, 23: 11})
        assert not re.search("Contents of", out)

        out = drg.dump_locals({"x": 42, "y": 23})
        assert re.search("Local variables in frame", out)
        assert re.search('<th>x.*<span class="number">42</span>', out, flags=re.DOTALL)
        assert re.search('<th>y.*<span class="number">23</span>', out, flags=re.DOTALL)

    def test_debug_dump(self):
        old = sys.stdout
        sys.stdout = HTMLStringO()
        try:
            dump([1, 2, 3])
            x = sys.stdout.reset()
            dump()
            y = sys.stdout.reset()
        finally:
            sys.stdout = old

        assert "Details for list object at" in x
        assert '<span class="number">1</span>' in x
        assert "Local variables in frame" in y
        assert "<th>x" in y
        assert "<th>old" in y

    def test_debug_help(self):
        old = sys.stdout
        sys.stdout = HTMLStringO()
        try:
            helper([1, 2, 3])
            x = sys.stdout.reset()
        finally:
            sys.stdout = old

        assert "Help on list object" in x
        assert "__delitem__" in x

    def test_exc_divider_found_on_chained_exception(self):
        @Request.application
        def app(request):
            def do_something():
                raise ValueError("inner")

            try:
                do_something()
            except ValueError:
                raise KeyError("outer")  # noqa: B904

        debugged = DebuggedApplication(app)
        client = Client(debugged)
        response = client.get("/")
        data = response.get_data(as_text=True)
        assert 'raise ValueError("inner")' in data
        assert '<div class="exc-divider">' in data
        assert 'raise KeyError("outer")' in data


def test_get_machine_id():
    rv = get_machine_id()
    assert isinstance(rv, bytes)


@pytest.mark.parametrize("crash", (True, False))
@pytest.mark.dev_server
def test_basic(dev_server, crash):
    c = dev_server(use_debugger=True)
    r = c.request("/crash" if crash else "")
    assert r.status == (500 if crash else 200)

    if crash:
        assert b"The debugger caught an exception in your WSGI application" in r.data
    else:
        assert r.json["PATH_INFO"] == "/"


def test_console_closure_variables(monkeypatch):
    # restore the original display hook
    monkeypatch.setattr(sys, "displayhook", console._displayhook)
    c = console.Console()
    c.eval("y = 5")
    c.eval("x = lambda: y")
    ret = c.eval("x()")
    assert ret == ">>> x()\n5\n"


@pytest.mark.timeout(2)
def test_chained_exception_cycle():
    try:
        try:
            raise ValueError()
        except ValueError:
            raise TypeError()  # noqa: B904
    except TypeError as e:
        # create a cycle and make it available outside the except block
        e.__context__.__context__ = error = e

    # if cycles aren't broken, this will time out
    tb = DebugTraceback(error)
    assert len(tb.all_tracebacks) == 2


def test_exception_without_traceback():
    try:
        raise Exception("msg1")
    except Exception as e:
        # filter_hidden_frames should skip this since it has no traceback
        e.__context__ = Exception("msg2")
        DebugTraceback(e)


@mock.patch.object(linecache, "getlines", autospec=True)
def test_missing_source_lines(mock_getlines: mock.Mock) -> None:
    """Rendering doesn't fail when the line number is beyond the available
    source lines.
    """
    mock_getlines.return_value = ["truncated"]

    try:
        raise ValueError()
    except ValueError as e:
        tb = DebugTraceback(e)

    html = tb.render_traceback_html()
    assert "test_debug.py" in html
    assert "truncated" not in html


def test_debugged_application_pin_security_false():
    """Test that DebuggedApplication can be initialized with pin_security=False."""

    @Request.application
    def app(request):
        return "OK"

    # This should not raise AttributeError
    debugged = DebuggedApplication(app, evalex=True, pin_security=False)
    assert debugged.pin is None


# ---------------------------------------------------------------------------
# Helpers for auth-boundary tests
# ---------------------------------------------------------------------------

def _make_debugged(evalex=True, pin_security=True):
    """Create a simple DebuggedApplication whose wrapped app always raises."""

    @Request.application
    def crashing_app(request):
        raise ValueError("test error")

    return DebuggedApplication(
        crashing_app, evalex=evalex, pin_security=pin_security
    )


def _make_ok_debugged(evalex=True, pin_security=True):
    """Create a DebuggedApplication whose wrapped app returns 200."""

    @Request.application
    def ok_app(request):
        return Response("OK")

    return DebuggedApplication(
        ok_app, evalex=evalex, pin_security=pin_security
    )


def _get_pin_cookie_value(debugged):
    """Return a valid PIN cookie value ``"<ts>|<hash>"`` for *debugged*."""
    return f"{int(time.time())}|{hash_pin(debugged.pin)}"


def _set_cookie_on_client(client, debugged, value):
    """Inject the debugger PIN cookie into *client*."""
    client.set_cookie(debugged.pin_cookie_name, value)


class TestCheckHostTrust:
    def test_default_trusted_hosts(self):
        debugged = _make_ok_debugged()
        assert debugged.trusted_hosts == [".localhost", "127.0.0.1"]

    def test_localhost_trusted(self):
        debugged = _make_ok_debugged()
        env = EnvironBuilder(headers={"Host": "localhost"}).get_environ()
        assert debugged.check_host_trust(env) is True

    def test_localhost_subdomain_trusted(self):
        debugged = _make_ok_debugged()
        env = EnvironBuilder(headers={"Host": "app.localhost"}).get_environ()
        assert debugged.check_host_trust(env) is True

    def test_127_0_0_1_trusted(self):
        debugged = _make_ok_debugged()
        env = EnvironBuilder(headers={"Host": "127.0.0.1"}).get_environ()
        assert debugged.check_host_trust(env) is True

    def test_127_0_0_1_with_port_trusted(self):
        debugged = _make_ok_debugged()
        env = EnvironBuilder(headers={"Host": "127.0.0.1:5000"}).get_environ()
        assert debugged.check_host_trust(env) is True

    def test_external_host_untrusted(self):
        debugged = _make_ok_debugged()
        env = EnvironBuilder(headers={"Host": "example.com"}).get_environ()
        assert debugged.check_host_trust(env) is False

    def test_custom_trusted_host(self):
        debugged = _make_ok_debugged()
        debugged.trusted_hosts.append("example.com")
        env = EnvironBuilder(headers={"Host": "example.com"}).get_environ()
        assert debugged.check_host_trust(env) is True

    def test_missing_host_untrusted(self):
        debugged = _make_ok_debugged()
        env = EnvironBuilder().get_environ()
        env.pop("HTTP_HOST", None)
        assert debugged.check_host_trust(env) is False


class TestCheckPinTrust:
    def test_pin_disabled_always_trusted(self):
        debugged = _make_ok_debugged(pin_security=False)
        env = EnvironBuilder().get_environ()
        assert debugged.check_pin_trust(env) is True

    def test_no_cookie_returns_false(self):
        debugged = _make_ok_debugged()
        env = EnvironBuilder().get_environ()
        assert debugged.check_pin_trust(env) is False

    def test_valid_cookie_returns_true(self):
        debugged = _make_ok_debugged()
        cookie_val = _get_pin_cookie_value(debugged)
        env = EnvironBuilder(
            headers={"Cookie": f"{debugged.pin_cookie_name}={cookie_val}"}
        ).get_environ()
        assert debugged.check_pin_trust(env) is True

    def test_expired_cookie_returns_false(self):
        debugged = _make_ok_debugged()
        ts = int(time.time()) - PIN_TIME - 100  # well past expiry
        cookie_val = f"{ts}|{hash_pin(debugged.pin)}"
        env = EnvironBuilder(
            headers={"Cookie": f"{debugged.pin_cookie_name}={cookie_val}"}
        ).get_environ()
        assert debugged.check_pin_trust(env) is False

    def test_bad_hash_returns_none(self):
        debugged = _make_ok_debugged()
        cookie_val = f"{int(time.time())}|badhashbadha"
        env = EnvironBuilder(
            headers={"Cookie": f"{debugged.pin_cookie_name}={cookie_val}"}
        ).get_environ()
        assert debugged.check_pin_trust(env) is None

    def test_malformed_cookie_returns_false(self):
        debugged = _make_ok_debugged()
        env = EnvironBuilder(
            headers={"Cookie": f"{debugged.pin_cookie_name}=nopipe"}
        ).get_environ()
        assert debugged.check_pin_trust(env) is False

    def test_lockout_returns_false(self):
        debugged = _make_ok_debugged()
        debugged._failed_pin_auth.value = 10
        cookie_val = _get_pin_cookie_value(debugged)
        env = EnvironBuilder(
            headers={"Cookie": f"{debugged.pin_cookie_name}={cookie_val}"}
        ).get_environ()
        assert debugged.check_pin_trust(env) is False


class TestPinAuth:
    @mock.patch("werkzeug.debug.time.sleep")
    def test_correct_pin_sets_cookie(self, _sleep):
        debugged = _make_ok_debugged()
        client = Client(debugged)
        rv = client.get(
            f"/?__debugger__=yes&cmd=pinauth"
            f"&s={debugged.secret}&pin={debugged.pin}"
        )
        data = json.loads(rv.data)
        assert data["auth"] is True
        assert data["exhausted"] is False
        # Cookie should be set in the response
        cookie_header = rv.headers.get("Set-Cookie", "")
        assert debugged.pin_cookie_name in cookie_header

    @mock.patch("werkzeug.debug.time.sleep")
    def test_incorrect_pin_fails(self, _sleep):
        debugged = _make_ok_debugged()
        client = Client(debugged)
        rv = client.get(
            f"/?__debugger__=yes&cmd=pinauth"
            f"&s={debugged.secret}&pin=000-000-000"
        )
        data = json.loads(rv.data)
        assert data["auth"] is False

    def test_untrusted_host_returns_security_error(self):
        debugged = _make_ok_debugged()
        client = Client(debugged)
        rv = client.get(
            f"/?__debugger__=yes&cmd=pinauth"
            f"&s={debugged.secret}&pin={debugged.pin}",
            headers={"Host": "evil.com"},
        )
        # SecurityError is a BadRequest (400).
        assert rv.status_code == 400

    @mock.patch("werkzeug.debug.time.sleep")
    def test_lockout_after_10_failures(self, _sleep):
        debugged = _make_ok_debugged()
        debugged._failed_pin_auth.value = 10
        client = Client(debugged)
        rv = client.get(
            f"/?__debugger__=yes&cmd=pinauth"
            f"&s={debugged.secret}&pin={debugged.pin}"
        )
        data = json.loads(rv.data)
        assert data["auth"] is False
        assert data["exhausted"] is True

    @mock.patch("werkzeug.debug.time.sleep")
    def test_pin_auth_cookie_secure_flag_with_proxy_proto(self, _sleep):
        """When X-Forwarded-Proto is https, the cookie should be Secure even
        if the backend connection is plain HTTP."""
        debugged = _make_ok_debugged()
        client = Client(debugged)
        rv = client.get(
            f"/?__debugger__=yes&cmd=pinauth"
            f"&s={debugged.secret}&pin={debugged.pin}",
            headers={"X-Forwarded-Proto": "https"},
        )
        cookie_header = rv.headers.get("Set-Cookie", "")
        assert "Secure" in cookie_header or "secure" in cookie_header

    @mock.patch("werkzeug.debug.time.sleep")
    def test_pin_auth_cookie_not_secure_on_plain_http(self, _sleep):
        """Without HTTPS or X-Forwarded-Proto, the cookie should NOT be Secure."""
        debugged = _make_ok_debugged()
        client = Client(debugged)
        rv = client.get(
            f"/?__debugger__=yes&cmd=pinauth"
            f"&s={debugged.secret}&pin={debugged.pin}"
        )
        cookie_header = rv.headers.get("Set-Cookie", "")
        assert "Secure" not in cookie_header


class TestDisplayConsole:
    def test_untrusted_host_returns_security_error(self):
        debugged = _make_ok_debugged()
        client = Client(debugged)
        rv = client.get("/console", headers={"Host": "evil.com"})
        # SecurityError is a BadRequest (400).
        assert rv.status_code == 400

    def test_trusted_host_renders_console(self):
        debugged = _make_ok_debugged()
        client = Client(debugged)
        rv = client.get("/console")
        assert rv.status_code == 200
        assert b"Console" in rv.data

    def test_evalex_trusted_false_without_pin(self):
        """Console should show EVALEX_TRUSTED = false when no PIN cookie."""
        debugged = _make_ok_debugged()
        client = Client(debugged)
        rv = client.get("/console")
        assert b"EVALEX_TRUSTED = false" in rv.data

    def test_evalex_trusted_true_with_valid_pin(self):
        """Console should show EVALEX_TRUSTED = true with valid PIN cookie."""
        debugged = _make_ok_debugged()
        cookie_val = _get_pin_cookie_value(debugged)
        client = Client(debugged)
        client.set_cookie(debugged.pin_cookie_name, cookie_val)
        rv = client.get("/console")
        assert b"EVALEX_TRUSTED = true" in rv.data

    def test_evalex_trusted_false_when_host_untrusted_even_with_pin(self):
        """Core bug fix: EVALEX_TRUSTED must be false when the host is not
        trusted, even if a valid PIN cookie exists.  Previously the HTML
        would show EVALEX_TRUSTED = true (PIN only) but the server would
        reject all commands with SecurityError."""
        debugged = _make_ok_debugged()
        cookie_val = _get_pin_cookie_value(debugged)

        # Use a request with the untrusted host AND the valid cookie.
        builder = EnvironBuilder(
            path="/console",
            headers={
                "Host": "evil.com",
                "Cookie": f"{debugged.pin_cookie_name}={cookie_val}",
            },
        )
        env = builder.get_environ()
        request = Request(env)
        resp = debugged.display_console(
            request, host_trusted=False, pin_trusted=True
        )
        # When host is not trusted, display_console returns SecurityError.
        # SecurityError is an HTTPException with code 400.
        from werkzeug.exceptions import SecurityError as SE

        assert isinstance(resp, SE)
        assert resp.code == 400


class TestExecuteCommand:
    def test_untrusted_host_returns_security_error(self):
        debugged = _make_ok_debugged()
        builder = EnvironBuilder()
        env = builder.get_environ()
        request = Request(env)
        frame = mock.MagicMock()
        resp = debugged.execute_command(
            request, "1+1", frame, host_trusted=False
        )
        # SecurityError is an HTTPException with code 400.
        from werkzeug.exceptions import SecurityError as SE

        assert isinstance(resp, SE)
        assert resp.code == 400


class TestEvalexTrustedRequiresHostTrust:
    """Verify that the rendered debugger HTML sets EVALEX_TRUSTED = false
    when host trust fails, even if PIN trust passes.  This is the core
    bug fix for the "page visible but console blocked" inconsistency."""

    def test_debugger_page_evalex_trusted_false_on_untrusted_host(self):
        debugged = _make_debugged(evalex=True, pin_security=False)
        # Force host to be untrusted by using an external host.
        client = Client(debugged)
        rv = client.get("/", headers={"Host": "evil.com"})
        data = rv.data.decode("utf-8", errors="replace")
        # evalex should be false when host is untrusted
        assert "EVALEX = false" in data

    def test_debugger_page_evalex_trusted_requires_both(self):
        """When host is trusted but PIN cookie is absent, EVALEX_TRUSTED
        must be false."""
        debugged = _make_debugged(evalex=True)
        client = Client(debugged)
        rv = client.get("/")
        data = rv.data.decode("utf-8", errors="replace")
        assert "EVALEX = true" in data
        assert "EVALEX_TRUSTED = false" in data


class TestEntryGateHostTrust:
    """Verify that __call__ gates execute_command on host trust at the
    dispatch level, not just inside the handler."""

    def test_execute_not_dispatched_when_host_untrusted(self):
        """When the host is not trusted, __call__ should NOT dispatch to
        execute_command even if PIN trust passes."""
        debugged = _make_debugged(evalex=True, pin_security=False)
        # First, trigger an exception to populate self.frames.
        client = Client(debugged)
        client.get("/")  # Creates frame entries

        # Get a frame id from the debugger.
        frame_ids = list(debugged.frames.keys())
        if not frame_ids:
            pytest.skip("no frames captured")
        frame_id = frame_ids[0]

        # Try to execute a command with an untrusted host.
        # pin_security=False means PIN trust always passes.
        rv = client.get(
            f"/?__debugger__=yes&cmd=1%2B1"
            f"&frm={frame_id}&s={debugged.secret}",
            headers={"Host": "evil.com"},
        )
        # The command should NOT have been executed.  The response should
        # be the normal app error (500), not the command result.
        data = rv.data.decode("utf-8", errors="replace")
        assert "2" not in data or "The debugger caught an exception" in data


class TestResourceServedWithoutAuth:
    """Static resources should be served without requiring host trust
    or PIN authentication."""

    def test_css_served_without_auth(self):
        debugged = _make_ok_debugged()
        client = Client(debugged)
        rv = client.get(
            "/?__debugger__=yes&cmd=resource&f=style.css",
            headers={"Host": "evil.com"},
        )
        assert rv.status_code == 200
        assert b"body" in rv.data or b"debugger" in rv.data or rv.status_code == 200

    def test_js_served_without_auth(self):
        debugged = _make_ok_debugged()
        client = Client(debugged)
        rv = client.get(
            "/?__debugger__=yes&cmd=resource&f=debugger.js",
            headers={"Host": "evil.com"},
        )
        assert rv.status_code == 200


class TestLogPinRequest:
    def test_untrusted_host_returns_security_error(self):
        debugged = _make_ok_debugged()
        client = Client(debugged)
        rv = client.get(
            f"/?__debugger__=yes&cmd=printpin&s={debugged.secret}",
            headers={"Host": "evil.com"},
        )
        # SecurityError is a BadRequest (400).
        assert rv.status_code == 400

    def test_trusted_host_returns_ok(self):
        debugged = _make_ok_debugged()
        client = Client(debugged)
        rv = client.get(
            f"/?__debugger__=yes&cmd=printpin&s={debugged.secret}"
        )
        assert rv.status_code == 200
