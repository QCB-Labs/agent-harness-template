"""Outbound integration and the actual privacy-decorator contracts."""

import ast
import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import authority, credentials, harness_mcp as harness, mail, market_mcp as market
from tools import names, outgoing_check, phase, square_mcp as square

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
CONVERSATION = "11111111-1111-4111-8111-111111111111"
OTHER_CONVERSATION = "22222222-2222-4222-8222-222222222222"
FINDING = outgoing_check.Finding("private-pattern", "illustrative finding")
MODULES = (harness, square, market)


def _guarded(decorator):
    found = []
    for module in MODULES:
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                names = [item.func.id if isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
                         else item.id if isinstance(item, ast.Name) else "" for item in node.decorator_list]
                if decorator in names:
                    found.append((module.__name__, node.name))
    return found


PRIVATE_TOOLS = _guarded("enters_private")
PHASE_TOOLS = _guarded("requires_phase")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    for module, name in [(harness, "REPO"), (square, "REPO_ROOT"), (market, "REPO_ROOT"),
                         (phase, "REPO_ROOT"), (authority, "REPO_ROOT"), (credentials, "REPO_ROOT"),
                         (credentials, "TOOLS_DIR")]:
        monkeypatch.setattr(module, name, tmp_path)
    monkeypatch.setenv("AGENT_SESSION_DIR", str(tmp_path / "session"))
    monkeypatch.setenv("AGENT_TRUST_DIR", str(tmp_path / "trust"))
    monkeypatch.setattr(phase, "_now", lambda: NOW)
    monkeypatch.setattr(authority, "_now", lambda: NOW)
    monkeypatch.setattr(harness, "_utc_now", lambda: NOW)
    monkeypatch.setattr(harness, "_recut_after_write", lambda *args: None)
    monkeypatch.setattr(harness, "_ensure_cards", lambda: None)
    monkeypatch.setattr(credentials, "_load_secrets_module", lambda: None)
    monkeypatch.setattr(credentials, "get_square_token", lambda: "illustrative-token")
    monkeypatch.setattr(credentials, "get_market_token", lambda: "illustrative-token")
    monkeypatch.setattr(harness, "get_agent_email", lambda: ("sender@example.com", "illustrative-password"))
    monkeypatch.setattr(harness, "resolve_correspondent", lambda key: "peer@example.com")
    monkeypatch.setattr(harness, "_load_secrets", lambda: SimpleNamespace(
        GMAIL_ADDRESS="sender@example.com", GMAIL_APP_PASSWORD="illustrative-password",
        NOTIFY_EMAIL_TO="operator@example.com"))
    monkeypatch.setattr(harness, "_pay_armed", lambda: False)
    monkeypatch.setattr(mail, "file_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(square.keys, "create_key_bind_payload", lambda **kwargs: {"public_key": "illustrative-public-key"})
    stored = {"message_id": "message@example.com", "from_address": "peer@example.com", "subject": "Greeting",
              "scope": "peer", "counterpart": "peer", "thread_references": []}
    monkeypatch.setattr(mail, "find_filed_message", lambda *args, **kwargs: stored)
    calls = {"http": [], "smtp": [], "check": [], "stored": stored}
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"status":"ok","listings":[],"results":[]}'
    def urlopen(request, *args, **kwargs):
        calls["http"].append(request)
        return Response()
    class SMTP:
        def __init__(self, *args, **kwargs):
            calls["smtp"].append({"messages": []})
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def starttls(self):
            pass
        def login(self, *args):
            pass
        def send_message(self, message):
            calls["smtp"][-1]["messages"].append(message)
        def sendmail(self, sender, recipients, message):
            calls["smtp"][-1]["messages"].append(message)
    monkeypatch.setattr(square.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(mail.smtplib, "SMTP", SMTP)
    def check(text, html=None):
        calls["check"].append((text, html))
        return []
    for module in (harness, mail, outgoing_check, square, market):
        monkeypatch.setattr(module, "check_outgoing", check, raising=False)
    phase._write(phase.PRIVATE)
    return calls


def _finding(monkeypatch, calls):
    def check(text, html=None):
        calls["check"].append((text, html))
        return [FINDING]
    for module in (harness, mail, outgoing_check, square, market):
        monkeypatch.setattr(module, "check_outgoing", check, raising=False)


def _refused(result):
    if isinstance(result, str):
        return result.startswith("REFUSED")
    if isinstance(result, list):
        return bool(result) and all(_refused(item) for item in result)
    return isinstance(result, dict) and (result.get("status") == "REFUSED" or bool(result.get("error")))


def _tool(module_name, name):
    return getattr(next(module for module in MODULES if module.__name__ == module_name), name)


def _arguments(fn):
    examples = {"message_id": "message@example.com", "body": "Illustrative body", "content": "Illustrative content",
                "correspondent_key": "peer", "subject": "Greeting", "proposed_name": "new-peer",
                "proposed_email": "new-peer@example.com", "reason": "Illustrative reason", "slug": "sample",
                "target_type": "note", "target_place_id": 3, "listing_id": 7}
    return {name: examples[name] for name, parameter in inspect.signature(fn).parameters.items()
            if parameter.default is inspect.Parameter.empty}


@pytest.mark.parametrize("module_name,name", PRIVATE_TOOLS)
@pytest.mark.parametrize("starting_phase", [phase.ORIENT, phase.PUBLIC_REPLY])
def test_private_tool_transitions_and_records_receipt(sandbox, module_name, name, starting_phase):
    phase._write(starting_phase)
    fn = _tool(module_name, name)
    fn(**_arguments(fn), _conversationId=CONVERSATION)
    assert phase.current() == phase.PRIVATE
    receipts = list(phase._private_use_root().rglob("*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text()) == {
        "conversation_id": CONVERSATION, "private_used_utc": NOW.isoformat()}
    first = receipts[0].read_bytes()
    fn(**_arguments(fn), _conversationId=CONVERSATION)
    assert len(list(phase._private_use_root().rglob("*.json"))) == 2
    assert receipts[0].read_bytes() == first
    assert phase.unknown_email_status(CONVERSATION)["available"] is False


@pytest.mark.parametrize("module_name,name", PRIVATE_TOOLS)
@pytest.mark.parametrize("identity", [None, "", "malformed", 123])
def test_private_tool_invalid_identity_refuses(sandbox, module_name, name, identity, tmp_path):
    phase._write(phase.ORIENT)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    fn = _tool(module_name, name)
    kwargs = {} if identity is None else {"_conversationId": identity}
    with pytest.raises(phase.PhaseError, match="conversation ID"):
        fn(**_arguments(fn), **kwargs)
    assert phase.current() == phase.ORIENT
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
    assert sandbox["smtp"] == sandbox["http"] == []


@pytest.mark.parametrize("module_name,name", PHASE_TOOLS)
def test_phase_tool_disallowed_phase_refuses(sandbox, monkeypatch, module_name, name):
    # reply_to_message explicitly allows every normal phase. Inject an unknown phase
    # to prove its decorator still rejects a phase outside that declared contract.
    disallowed = "unknown" if name == "reply_to_message" else phase.ORIENT
    monkeypatch.setattr(phase, "current", lambda: disallowed)
    fn = _tool(module_name, name)
    with pytest.raises(phase.PhaseError, match="not available"):
        fn(**_arguments(fn), **({"_conversationId": CONVERSATION} if name == "reply_to_message" else {}))
    assert sandbox["smtp"] == []


MAIL_PATHS = ["reply_peer", "reply_operator", "new_peer", "new_operator", "write_operator_message",
              "write_journal", "authorise_spend", "notify_crash"]
OPERATOR_PATHS = set(MAIL_PATHS) - {"reply_peer", "new_peer"}


def _mail_call(name, calls):
    if name.startswith("reply_"):
        operator = name == "reply_operator"
        calls["stored"].update(scope="operator" if operator else "peer", counterpart="operator" if operator else "peer")
        return harness.reply_to_message("message@example.com", "Illustrative body", _conversationId=CONVERSATION)
    if name.startswith("new_"):
        return harness.send_new_thread("operator" if name == "new_operator" else "peer", "Greeting", "Illustrative body")
    if name == "write_operator_message":
        return harness.write_operator_message("Illustrative body", _conversationId=CONVERSATION)
    if name == "write_journal":
        return harness.write_journal("# Journal\nIllustrative body")
    if name == "authorise_spend":
        return harness.authorise_spend("invent_kind", "Illustrative detail")
    return harness.notify_crash("Illustrative summary")


@pytest.mark.parametrize("name", [pytest.param(name, marks=pytest.mark.xfail(strict=True,
    reason="Operator email findings warn and send instead of refusing.")) if name in OPERATOR_PATHS else name
    for name in MAIL_PATHS])
def test_mail_caller_finding_refuses(sandbox, monkeypatch, name):
    _finding(monkeypatch, sandbox)
    result = _mail_call(name, sandbox)
    assert sandbox["smtp"] == []
    assert sandbox["check"]
    if name in ("reply_peer", "reply_operator", "new_peer", "new_operator"):
        assert _refused(result)
    if isinstance(result, dict):
        assert result["emailed"] is False


@pytest.mark.parametrize("name", MAIL_PATHS)
def test_mail_caller_clean_sends(sandbox, name):
    _mail_call(name, sandbox)
    assert len(sandbox["smtp"]) == 1
    assert len(sandbox["smtp"][0]["messages"]) == 1
    assert len(sandbox["check"]) == 1
    assert "Illustrative" in sandbox["check"][0][0]


HTTP_TEXT = ["post_dispatch", "post_comment", "search_square", "create_world_draft",
             "create_listing", "write_store_line", "browse_shelves_aisle", "browse_shelves_sort"]
HTTP_AUTH = ["post_dispatch", "post_comment", "vote_post", "bind_signing_key", "read_square_profile",
             "read_square_quotas", "create_world_draft", "create_listing", "write_store_line",
             "activate_world_listing", "cancel_world_draft", "withdraw_listing", "read_merchant_profile"]


def _http_call(name):
    if name == "post_dispatch":
        return square.post_dispatch("Illustrative title", "Illustrative body", "dispatch")
    if name == "post_comment":
        return square.post_comment(7, "Illustrative body")
    if name == "search_square":
        return square.search_square("Illustrative query")
    if name.startswith("browse_shelves_"):
        return market.browse_shelves(**{name.removeprefix("browse_shelves_"): "Illustrative query"})
    if name == "create_world_draft":
        return market.create_world_draft(7, "Illustrative title", "Illustrative description", "Illustrative preview",
                                         1, seller_wallet="0x" + "1" * 40)
    if name == "create_listing":
        return market.create_listing("Illustrative title", "Illustrative description", "Illustrative preview",
                                     "Illustrative artifact", 0, seller_wallet="0x" + "1" * 40)
    if name == "write_store_line":
        return market.write_store_line("Illustrative store line")
    if name == "vote_post":
        return square.vote_post(7)
    if name == "bind_signing_key":
        return square.bind_signing_key()
    if name == "activate_world_listing":
        return market.activate_world_listing(7, 8)
    if name == "cancel_world_draft":
        return market.cancel_world_draft(7)
    if name == "withdraw_listing":
        return market.withdraw_listing(7)
    module = square if name.startswith("read_square") else market
    return getattr(module, name)()


@pytest.mark.parametrize("name", HTTP_TEXT)
@pytest.mark.xfail(strict=True, reason="Square/Market outbound text paths never run the outgoing check.")
def test_http_text_caller_finding_refuses(sandbox, monkeypatch, name):
    _finding(monkeypatch, sandbox)
    result = _http_call(name)
    assert sandbox["http"] == []
    assert sandbox["check"]
    assert _refused(result)


@pytest.mark.parametrize("name", HTTP_TEXT)
def test_http_text_caller_clean_sends(sandbox, name):
    _http_call(name)
    assert len(sandbox["http"]) == 1
    request = sandbox["http"][0]
    assert "illustrative" in (request.full_url + (request.data or b"").decode()).lower()


@pytest.mark.parametrize("name", HTTP_AUTH)
def test_authenticated_http_path_missing_token_refuses(sandbox, monkeypatch, name):
    monkeypatch.setattr(credentials, "get_square_token", lambda: "")
    monkeypatch.setattr(credentials, "get_market_token", lambda: "")
    result = _http_call(name)
    assert result["error"] == "UNAUTHENTICATED"
    assert sandbox["http"] == []


@pytest.mark.parametrize("name", ["reply_peer", "reply_operator", "new_peer", "new_operator"])
@pytest.mark.parametrize("missing", ["address", "password"])
def test_mailbox_missing_credential_refuses(sandbox, monkeypatch, name, missing):
    monkeypatch.setattr(harness, "get_agent_email", lambda: (
        "" if missing == "address" else "sender@example.com",
        "" if missing == "password" else "illustrative-password"))
    assert _refused(_mail_call(name, sandbox))
    assert sandbox["smtp"] == []


@pytest.mark.parametrize("name", ["write_operator_message", "write_journal", "authorise_spend", "notify_crash"])
@pytest.mark.parametrize("missing", ["module", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL_TO"])
def test_operator_caller_missing_secret_no_send(sandbox, monkeypatch, name, missing):
    values = {"GMAIL_ADDRESS": "sender@example.com", "GMAIL_APP_PASSWORD": "illustrative-password",
              "NOTIFY_EMAIL_TO": "operator@example.com"}
    values.pop(missing, None)
    monkeypatch.setattr(harness, "_load_secrets", lambda: None if missing == "module" else SimpleNamespace(**values))
    result = _mail_call(name, sandbox)
    assert sandbox["smtp"] == []
    if isinstance(result, dict):
        assert result["emailed"] is False


@pytest.mark.parametrize("missing", ["module", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL_TO"])
def test_operator_send_helper_missing_secret_returns_false(sandbox, monkeypatch, missing):
    values = {"GMAIL_ADDRESS": "sender@example.com", "GMAIL_APP_PASSWORD": "illustrative-password",
              "NOTIFY_EMAIL_TO": "operator@example.com"}
    values.pop(missing, None)
    monkeypatch.setattr(harness, "_load_secrets", lambda: None if missing == "module" else SimpleNamespace(**values))
    assert harness._send_email("Greeting", "Illustrative body") is False
    assert sandbox["smtp"] == []


@pytest.mark.parametrize("identity", [None, "", "malformed", 123])
def test_reply_invalid_identity_refuses(sandbox, identity):
    kwargs = {} if identity is None else {"_conversationId": identity}
    assert _refused(harness.reply_to_message("message@example.com", "Illustrative body", **kwargs))
    assert sandbox["smtp"] == []


@pytest.mark.parametrize("restriction", ["same_conversation", "recent_private", "corrupt_receipt", "automated", "limit"])
def test_reply_restrictions_refuse(sandbox, monkeypatch, restriction):
    sandbox["stored"].update(scope="mailbag", counterpart=mail.MAILBAG)
    if restriction == "same_conversation":
        phase._record_private_use(CONVERSATION)
    elif restriction == "recent_private":
        phase._record_private_use(OTHER_CONVERSATION)
    elif restriction == "corrupt_receipt":
        phase._record_private_use(CONVERSATION)
        next(phase._private_use_root().rglob("*.json")).write_text("{broken", encoding="utf-8")
    elif restriction == "automated":
        sandbox["stored"]["is_autoreply"] = True
    else:
        for _ in range(mail.REPLY_MAX_PER_THREAD):
            mail.record_reply(sandbox["stored"], repo=harness.REPO)
    assert _refused(harness.reply_to_message("message@example.com", "Illustrative body", _conversationId=CONVERSATION))
    assert sandbox["smtp"] == []


def test_reply_missing_stored_message_refuses(sandbox, monkeypatch):
    monkeypatch.setattr(mail, "find_filed_message", lambda *args, **kwargs: None)
    assert _refused(harness.reply_to_message("missing@example.com", "Illustrative body", _conversationId=CONVERSATION))
    assert sandbox["smtp"] == []


def test_new_thread_unknown_correspondent_refuses(sandbox, monkeypatch):
    def resolve(key):
        raise harness.UnknownCorrespondent("unknown illustrative key")
    monkeypatch.setattr(harness, "resolve_correspondent", resolve)
    assert _refused(harness.send_new_thread("unknown", "Greeting", "Illustrative body"))
    assert sandbox["smtp"] == []



def test_reply_once_per_wake_refuses_second_reply(sandbox):
    assert harness.reply_to_message("message@example.com", "Illustrative body", _conversationId=CONVERSATION).startswith("sent")
    sandbox["stored"]["message_id"] = "another@example.com"
    assert _refused(harness.reply_to_message("another@example.com", "Illustrative body", _conversationId=CONVERSATION))
    assert len(sandbox["smtp"]) == 1


def test_reply_peer_allowed_after_private_use(sandbox):
    phase._record_private_use(CONVERSATION)
    assert harness.reply_to_message("message@example.com", "Illustrative body", _conversationId=CONVERSATION).startswith("sent")
    assert len(sandbox["smtp"]) == 1


def test_listing_fee_missing_wallet_session_refuses(sandbox, monkeypatch):
    from tools import wallet
    monkeypatch.setattr(wallet, "load_wallet_config", lambda: {
        "public_address": "0x" + "1" * 40, "caps_usdc": {"per_tx": 2}})
    monkeypatch.setattr(wallet, "money_actions_allowed", lambda **kwargs: True)
    monkeypatch.setattr(wallet, "pay_armed", lambda cfg: True)
    monkeypatch.setattr(authority, "session_active", lambda **kwargs: True)
    monkeypatch.setattr(market, "_read_market_official", lambda: {
        "treasury": "0x" + "2" * 40, "usdc_contract": "0x" + "3" * 40,
        "network": "base", "listing_fee_usdc": 1})
    monkeypatch.setattr(wallet, "wallet_session_ok", lambda: False)
    transfers = []
    monkeypatch.setattr(wallet, "transfer_token", lambda *args: transfers.append(args))
    assert market.pay_listing_fee("Illustrative listing")["error"] == "CIRCLE_LOGGED_OUT"
    assert transfers == []
    assert sandbox["http"] == []


@pytest.mark.parametrize("name", ["acquire_free_artifact", "purchase_listing"])
@pytest.mark.xfail(strict=True, reason="Missing Market token falls through to public download and records an acquisition.")
def test_free_acquisition_missing_token_refuses(sandbox, monkeypatch, name, tmp_path):
    monkeypatch.setattr(credentials, "get_market_token", lambda: "")
    monkeypatch.setattr(market, "inspect_listing", lambda listing_id: {
        "title": "Illustrative artifact", "price_usdc": 0, "description": "Illustrative artifact text"})
    result = getattr(market, name)(7, "Illustrative purpose")
    assert _refused(result)
    assert not (tmp_path / names.memory("acquired")).exists()
    assert not any(request.method == "POST" for request in sandbox["http"])
