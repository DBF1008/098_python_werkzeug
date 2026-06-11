# Fix DebuggedApplication Authentication Boundary Bug

## Context

`DebuggedApplication` behind a reverse proxy shows inconsistent behavior: the same session can display the debug page but fail to execute console commands. The root cause is that host-trust and PIN-trust checks are scattered across multiple handlers, each making independent decisions from `environ`. When proxy headers cause any divergence in these independent reads, handlers disagree.

## Bug Analysis

### Bug 1 — `evalex_trusted` in rendered HTML ignores host trust

`debug_application` (line 359) and `display_console` (line 412) both set `evalex_trusted` based on `check_pin_trust` alone:

```python
# debug_application, line 359
is_trusted = bool(self.check_pin_trust(environ))
# display_console, line 412
is_trusted = bool(self.check_pin_trust(request.environ))
```

If `check_pin_trust` returns `True` (valid cookie exists or `pin_security=False`) but `check_host_trust` fails, the HTML renders `EVALEX_TRUSTED = true`. JavaScript enables the interactive console. But when the user sends a command, `execute_command` checks host trust internally and returns `SecurityError`. The user sees the console but can't execute anything — the exact symptom reported.

### Bug 2 — `__call__` entry gate doesn't check host trust for execute_command

```python
# __call__, lines 558-565
elif (
    self.evalex
    and cmd is not None
    and frame is not None
    and self.secret == secret
    and self.check_pin_trust(environ)   # ← only PIN trust, no host trust!
):
    response = self.execute_command(request, cmd, frame)
```

The entry gate allows `execute_command` to be dispatched based solely on PIN trust. The host trust check happens later inside `execute_command`, creating a two-stage inconsistency: entry says "go", handler says "stop".

### Bug 3 — Trust checks scattered across 5 handlers

| Handler | check_host_trust | check_pin_trust |
|---------|:---:|:---:|
| `debug_application` | ✓ (for evalex flag) | ✓ (for evalex_trusted) |
| `execute_command` | ✓ (returns SecurityError) | — (gated in `__call__`) |
| `display_console` | ✓ (returns SecurityError) | ✓ (for evalex_trusted) |
| `pin_auth` | ✓ (returns SecurityError) | ✓ (full auth logic) |
| `log_pin_request` | ✓ (returns SecurityError) | — |
| `get_resource` | — | — |

Each handler independently reads `environ` and makes its own trust decision. In proxy setups where `HTTP_HOST` or `wsgi.url_scheme` might not be stable, these independent reads can disagree.

## Fix Strategy

**Centralize trust decisions in `__call__`, pass them to handlers.** The trust state is determined once from `environ` and all handlers use the same decision — no re-reading, no re-deciding.

### File: `src/werkzeug/debug/__init__.py`

#### 1. Update handler signatures to accept trust decisions

Each handler gains optional trust parameters (with `None` defaults for backward compatibility — if not passed, the handler falls back to its own check):

- `execute_command(request, command, frame, *, host_trusted=None)` — if `host_trusted is None`, call `check_host_trust`; otherwise use the passed value.
- `display_console(request, *, host_trusted=None, pin_trusted=None)` — same pattern.
- `pin_auth(request, *, host_trusted=None)` — same pattern.
- `log_pin_request(request, *, host_trusted=None)` — same pattern.

#### 2. Rewrite `__call__` to be the single trust decision point

```python
def __call__(self, environ, start_response):
    request = Request(environ)
    response = self.debug_application

    # Single trust decision point — computed once from environ.
    host_trusted = self.check_host_trust(environ)
    pin_trusted = self.check_pin_trust(environ)

    if request.args.get("__debugger__") == "yes":
        cmd = request.args.get("cmd")
        arg = request.args.get("f")
        secret = request.args.get("s")
        frame = self.frames.get(request.args.get("frm", type=int))

        if cmd == "resource" and arg:
            response = self.get_resource(request, arg)
        elif cmd == "pinauth" and secret == self.secret:
            response = self.pin_auth(request, host_trusted=host_trusted)
        elif cmd == "printpin" and secret == self.secret:
            response = self.log_pin_request(request, host_trusted=host_trusted)
        elif (
            self.evalex
            and host_trusted          # ← NEW: gate on host trust at entry
            and cmd is not None
            and frame is not None
            and self.secret == secret
            and pin_trusted           # use pre-computed value
        ):
            response = self.execute_command(
                request, cmd, frame, host_trusted=host_trusted
            )
    elif (
        self.evalex
        and self.console_path is not None
        and request.path == self.console_path
    ):
        response = self.display_console(
            request, host_trusted=host_trusted, pin_trusted=pin_trusted
        )
    return response(environ, start_response)
```

#### 3. Fix `debug_application` — `evalex_trusted` must include host trust

```python
# In debug_application, lines 359-364:
host_trusted = self.check_host_trust(environ)
pin_trusted = bool(self.check_pin_trust(environ))
is_trusted = host_trusted and pin_trusted   # ← both must pass
html = tb.render_debugger_html(
    evalex=self.evalex and host_trusted,
    secret=self.secret,
    evalex_trusted=is_trusted,
)
```

#### 4. Fix `pin_auth` cookie `secure` flag

When behind proxies, `request.is_secure` may not reflect the external TLS state. Check `X-Forwarded-Proto` as a secondary signal:

```python
# In pin_auth:
is_secure = request.is_secure
if not is_secure:
    forwarded_proto = request.environ.get("HTTP_X_FORWARDED_PROTO", "")
    if forwarded_proto.strip().split(",")[0].strip().lower() == "https":
        is_secure = True

rv.set_cookie(
    self.pin_cookie_name,
    f"{int(time.time())}|{hash_pin(pin)}",
    httponly=True,
    samesite="Strict",
    secure=is_secure,
)
```

This ensures the PIN cookie is marked `Secure` when the external connection is HTTPS (even if the proxy terminates TLS), preventing the cookie from being sent over plain HTTP.

### File: `tests/test_debug.py`

Add comprehensive tests for the authentication boundary:

1. **`test_check_host_trust_default`** — verify default trusted hosts (`.localhost`, `127.0.0.1`) accept/reject correctly
2. **`test_check_host_trust_custom`** — verify custom trusted hosts list
3. **`test_check_pin_trust_no_cookie`** — returns `False` when no cookie
4. **`test_check_pin_trust_valid`** — returns `True` with valid cookie
5. **`test_check_pin_trust_expired`** — returns `False` when cookie expired
6. **`test_check_pin_trust_bad_hash`** — returns `None` when PIN hash doesn't match
7. **`test_pin_auth_correct_pin`** — full auth flow with correct PIN
8. **`test_pin_auth_incorrect_pin`** — auth fails with wrong PIN
9. **`test_pin_auth_untrusted_host`** — returns SecurityError when host not trusted
10. **`test_pin_auth_lockout`** — lockout after 10 failures
11. **`test_execute_command_untrusted_host`** — SecurityError when host not trusted
12. **`test_display_console_untrusted_host`** — SecurityError when host not trusted
13. **`test_display_console_trusted`** — page renders with correct `evalex_trusted`
14. **`test_evalex_trusted_requires_host_trust`** — verify `EVALEX_TRUSTED` is `false` when host not trusted even if PIN cookie valid (the core bug fix)
15. **`test_pin_cookie_secure_flag_with_proxy`** — verify `secure=True` when `X-Forwarded-Proto: https`
16. **`test_resource_served_without_auth`** — static resources don't require auth
17. **`test_entry_gate_checks_host_trust`** — `__call__` gates execute_command on host trust

## Security Defaults Preserved

- `trusted_hosts` defaults to `[".localhost", "127.0.0.1"]` — unchanged
- PIN security enabled by default — unchanged
- Cookie: `httponly=True`, `samesite="Strict"` — unchanged
- 10-attempt lockout with progressive delay — unchanged
- Static resources: no auth required (they're sanitized static files) — unchanged
