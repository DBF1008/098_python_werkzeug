import pytest

from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.routing import Map
from werkzeug.routing import Rule
from werkzeug.test import Client
from werkzeug.test import create_environ
from werkzeug.utils import redirect
from werkzeug.wrappers import Request
from werkzeug.wrappers import Response


@pytest.mark.parametrize(
    ("kwargs", "base", "url_root"),
    (
        pytest.param(
            {},
            {
                "REMOTE_ADDR": "192.168.0.2",
                "HTTP_HOST": "spam",
                "HTTP_X_FORWARDED_FOR": "192.168.0.1",
                "HTTP_X_FORWARDED_PROTO": "https",
            },
            "https://spam/",
            id="for",
        ),
        pytest.param(
            {"x_proto": 1},
            {"HTTP_HOST": "spam", "HTTP_X_FORWARDED_PROTO": "https"},
            "https://spam/",
            id="proto",
        ),
        pytest.param(
            {"x_host": 1},
            {"HTTP_HOST": "spam", "HTTP_X_FORWARDED_HOST": "eggs"},
            "http://eggs/",
            id="host",
        ),
        pytest.param(
            {"x_port": 1},
            {"HTTP_HOST": "spam", "HTTP_X_FORWARDED_PORT": "8080"},
            "http://spam:8080/",
            id="port, host without port",
        ),
        pytest.param(
            {"x_port": 1},
            {"HTTP_HOST": "spam:9000", "HTTP_X_FORWARDED_PORT": "8080"},
            "http://spam:8080/",
            id="port, host with port",
        ),
        pytest.param(
            {"x_port": 1},
            {
                "SERVER_NAME": "spam",
                "SERVER_PORT": "9000",
                "HTTP_X_FORWARDED_PORT": "8080",
            },
            "http://spam:8080/",
            id="port, name",
        ),
        pytest.param(
            {"x_prefix": 1},
            {"HTTP_HOST": "spam", "HTTP_X_FORWARDED_PREFIX": "/eggs"},
            "http://spam/eggs/",
            id="prefix",
        ),
        pytest.param(
            {"x_for": 1, "x_proto": 1, "x_host": 1, "x_port": 1, "x_prefix": 1},
            {
                "REMOTE_ADDR": "192.168.0.2",
                "HTTP_HOST": "spam:9000",
                "HTTP_X_FORWARDED_FOR": "192.168.0.1",
                "HTTP_X_FORWARDED_PROTO": "https",
                "HTTP_X_FORWARDED_HOST": "eggs",
                "HTTP_X_FORWARDED_PORT": "443",
                "HTTP_X_FORWARDED_PREFIX": "/ham",
            },
            "https://eggs/ham/",
            id="all",
        ),
        pytest.param(
            {"x_for": 2},
            {
                "REMOTE_ADDR": "192.168.0.3",
                "HTTP_HOST": "spam",
                "HTTP_X_FORWARDED_FOR": "192.168.0.1, 192.168.0.2",
            },
            "http://spam/",
            id="multiple for",
        ),
        pytest.param(
            {"x_for": 0},
            {
                "REMOTE_ADDR": "192.168.0.1",
                "HTTP_HOST": "spam",
                "HTTP_X_FORWARDED_FOR": "192.168.0.2",
            },
            "http://spam/",
            id="ignore 0",
        ),
        pytest.param(
            {"x_for": 3},
            {
                "REMOTE_ADDR": "192.168.0.1",
                "HTTP_HOST": "spam",
                "HTTP_X_FORWARDED_FOR": "192.168.0.3, 192.168.0.2",
            },
            "http://spam/",
            id="ignore len < trusted",
        ),
        pytest.param(
            {},
            {
                "REMOTE_ADDR": "192.168.0.2",
                "HTTP_HOST": "spam",
                "HTTP_X_FORWARDED_FOR": "192.168.0.3, 192.168.0.1",
            },
            "http://spam/",
            id="ignore untrusted",
        ),
        pytest.param(
            {"x_for": 2},
            {
                "REMOTE_ADDR": "192.168.0.1",
                "HTTP_HOST": "spam",
                "HTTP_X_FORWARDED_FOR": ", 192.168.0.3",
            },
            "http://spam/",
            id="ignore empty",
        ),
        pytest.param(
            {"x_for": 2, "x_prefix": 1},
            {
                "REMOTE_ADDR": "192.168.0.2",
                "HTTP_HOST": "spam",
                "HTTP_X_FORWARDED_FOR": "192.168.0.1, 192.168.0.3",
                "HTTP_X_FORWARDED_PREFIX": "/ham, /eggs",
            },
            "http://spam/eggs/",
            id="prefix < for",
        ),
        pytest.param(
            {"x_host": 1},
            {"HTTP_HOST": "spam", "HTTP_X_FORWARDED_HOST": "[2001:db8::a]"},
            "http://[2001:db8::a]/",
            id="ipv6 host",
        ),
        pytest.param(
            {"x_port": 1},
            {"HTTP_HOST": "[2001:db8::a]", "HTTP_X_FORWARDED_PORT": "8080"},
            "http://[2001:db8::a]:8080/",
            id="ipv6 port, host without port",
        ),
        pytest.param(
            {"x_port": 1},
            {"HTTP_HOST": "[2001:db8::a]:9000", "HTTP_X_FORWARDED_PORT": "8080"},
            "http://[2001:db8::a]:8080/",
            id="ipv6 - port, host with port",
        ),
    ),
)
def test_proxy_fix(monkeypatch, kwargs, base, url_root):
    monkeypatch.setattr(Response, "autocorrect_location_header", True)

    @Request.application
    def app(request):
        # for header
        assert request.remote_addr == "192.168.0.1"
        # proto, host, port, prefix headers
        assert request.url_root == url_root

        urls = url_map.bind_to_environ(request.environ)
        parrot_url = urls.build("parrot")
        # build includes prefix
        assert urls.build("parrot") == "/".join((request.script_root, "parrot"))
        # match doesn't include prefix
        assert urls.match("/parrot")[0] == "parrot"

        # With autocorrect_location_header enabled, location header will
        # start with url_root
        return redirect(parrot_url)

    url_map = Map([Rule("/parrot", endpoint="parrot")])
    app = ProxyFix(app, **kwargs)

    base.setdefault("REMOTE_ADDR", "192.168.0.1")
    environ = create_environ(environ_overrides=base)

    # host is always added, remove it if the test doesn't set it
    if "HTTP_HOST" not in base:
        del environ["HTTP_HOST"]

    response = Client(app).open(Request(environ))
    assert response.location == f"{url_root}parrot"


def test_proxy_fix_host_and_subdomain_matching():
    """ProxyFix adjusts environ, then routing with both host_matching and
    subdomain_matching works correctly. Match uses the proxy-rewritten host,
    and build also produces correct URLs based on the same adapter state."""

    url_map = Map(
        [
            Rule("/", host="app.example.com", endpoint="index"),
            Rule("/", host="<sub>.app.example.com", endpoint="sub"),
            Rule("/page", host="app.example.com", endpoint="page"),
        ],
        host_matching=True,
        subdomain_matching=True,
    )

    @Request.application
    def app(request):
        urls = url_map.bind_to_environ(
            request.environ, server_name="app.example.com"
        )

        # Adapter should have the proxy-rewritten host
        assert urls.server_name == "de.app.example.com"
        # Subdomain should be extracted from the proxy-rewritten host
        assert urls.subdomain == "de"

        # Match uses the full host (host_matching behavior)
        endpoint, values = urls.match("/")
        assert endpoint == "sub"
        assert values == {"sub": "de"}

        # Build for same host returns relative URL
        assert urls.build("sub", {"sub": "de"}) == "/"
        # Build for different host returns absolute URL
        assert urls.build("sub", {"sub": "fr"}) == "http://fr.app.example.com/"
        # Build for base host returns absolute URL (different from current host)
        assert urls.build("index") == "http://app.example.com/"

        return Response("ok")

    app = ProxyFix(app, x_host=1)

    environ = create_environ(
        "/",
        base_url="http://internal.proxy",
        environ_overrides={
            "HTTP_X_FORWARDED_HOST": "de.app.example.com",
        },
    )

    response = Client(app).open(Request(environ))
    assert response.data == b"ok"


def test_proxy_fix_host_matching_with_prefix():
    """ProxyFix adjusts both host and script prefix. Match and build both
    use the proxy-rewritten values consistently."""

    url_map = Map(
        [
            Rule("/", host="app.example.com", endpoint="index"),
            Rule("/items", host="app.example.com", endpoint="items"),
        ],
        host_matching=True,
    )

    @Request.application
    def app(request):
        urls = url_map.bind_to_environ(request.environ)

        # Adapter should have the proxy-rewritten host and script
        assert urls.server_name == "app.example.com"
        assert urls.script_name == "/myapp/"

        endpoint, _ = urls.match("/items")
        assert endpoint == "items"

        # Build should include the proxy prefix
        assert urls.build("items") == "/myapp/items"
        # External build should use the proxy-rewritten host
        assert (
            urls.build("items", force_external=True)
            == "http://app.example.com/myapp/items"
        )

        return Response("ok")

    app = ProxyFix(app, x_host=1, x_prefix=1)

    environ = create_environ(
        "/items",
        base_url="http://internal.proxy",
        environ_overrides={
            "HTTP_X_FORWARDED_HOST": "app.example.com",
            "HTTP_X_FORWARDED_PREFIX": "/myapp",
        },
    )

    response = Client(app).open(Request(environ))
    assert response.data == b"ok"
