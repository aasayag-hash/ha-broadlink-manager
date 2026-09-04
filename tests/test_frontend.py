"""Checks on the static frontend.

There is no build step and no framework, so nothing catches a typo in an element
id: the browser just returns null and the feature silently does nothing. These
tests stand in for that missing safety net.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parent.parent / "broadlink_manager" / "frontend"


@pytest.fixture(scope="module")
def html() -> str:
    return (FRONTEND / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js() -> str:
    return (FRONTEND / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css() -> str:
    return (FRONTEND / "style.css").read_text(encoding="utf-8")


def test_every_id_the_js_looks_up_exists(html, js):
    """$("#foo") on a missing id returns null and the handler dies quietly."""
    in_html = set(re.findall(r'id="([^"]+)"', html))
    looked_up = set(re.findall(r'\$\("#([a-zA-Z0-9_-]+)"\)', js))
    missing = sorted(looked_up - in_html)
    assert not missing, f"el JS busca ids que no están en el HTML: {missing}"


def test_api_base_is_derived_from_the_document_url(js):
    """This is what makes the app work behind the ingress path prefix."""
    assert 'new URL(".", window.location.href).pathname' in js


def test_static_assets_are_referenced_relatively(html):
    """An absolute /static path breaks under the ingress prefix."""
    for match in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert not match.startswith("/"), f"ruta absoluta: {match}"


def test_no_fetch_uses_an_absolute_path(js):
    """Same reason: every call has to go through API_BASE."""
    absolute = re.findall(r'fetch\(\s*[`"\']/', js)
    assert not absolute, "hay fetch con ruta absoluta"


def test_every_fetch_goes_through_api_base(js):
    """Directly, or through a local that was itself built from API_BASE."""
    # Locals holding a prefix, e.g. `const base = `${API_BASE}api/...``.
    derived = {
        name
        for name, value in re.findall(r"const (\w+)\s*=\s*`([^`]*)`", js)
        if "API_BASE" in value
    }

    for _, url in re.findall(r"fetch\(\s*([`\"'])(.*?)\1", js, re.DOTALL):
        interpolated = set(re.findall(r"\$\{(\w+)", url))
        assert "API_BASE" in url or interpolated & derived, f"fetch sin API_BASE: {url}"


def test_colors_come_from_css_variables(css):
    """Defined in one place so a dark theme is a matter of redefining them."""
    assert ":root {" in css
    for token in ("--accent", "--surface", "--text", "--danger", "--ok", "--warn"):
        assert token in css


def test_user_facing_text_is_in_spanish(html):
    """UI in rioplatense Spanish, code in English -- the project's convention."""
    for word in ("Dispositivos", "Aprender", "Códigos", "Entidades"):
        assert word in html or word.replace("ó", "&oacute;") in html


def test_html_has_no_inline_script_or_style(html):
    """Everything lives in app.js and style.css, which keeps the CSP simple."""
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert "<style" not in html


def test_the_learn_flow_guards_against_double_starts(js):
    """A second click would start a parallel capture on the same device."""
    assert "if (scanning) return" in js


def test_destructive_actions_ask_first(js):
    """Deleting a code or a group cannot be undone from the UI."""
    assert js.count("window.confirm") >= 3


# --- virtual remote --------------------------------------------------------


def test_the_remote_has_its_own_tab(html):
    """Kept apart from the codes table: that one is for administration."""
    assert 'data-tab="control"' in html
    assert 'id="tab-control"' in html


def test_a_double_tap_cannot_fire_a_code_twice(js):
    """On a gate that would open and immediately close it."""
    send = re.search(r"async function sendFromPad.*?\n\}", js, re.DOTALL).group(0)
    assert "sending.has(key)" in send
    assert "sending.add(key)" in send
    assert "sending.delete(key)" in send


def test_a_press_is_acknowledged_visually(js):
    """The hardware reports nothing, so a press that did nothing would look
    exactly like one that worked."""
    send = re.search(r"async function sendFromPad.*?\n\}", js, re.DOTALL).group(0)
    assert '"sent"' in send and '"failed"' in send
    assert "setTimeout" in send


def test_switching_device_resets_the_remote_group(js):
    """Otherwise the selector keeps a group that belongs to the old device."""
    handler = re.search(r'closest\("\.device-card"\).*?\n  \}\);', js, re.DOTALL).group(0)
    assert "controlGroup = null" in handler


def test_the_remote_sends_through_the_existing_endpoint(js):
    """Sending goes through Home Assistant, not straight to the hardware."""
    send = re.search(r"async function sendFromPad.*?\n\}", js, re.DOTALL).group(0)
    assert "api/codes/" in send and "/send" in send


# --- wizard state, from bugs found in review -------------------------------


def test_switching_device_exits_the_wizard(js):
    """A wizard belongs to the device it was started on.

    Without this the panel stayed up after selecting another Broadlink, and the
    next capture learned on the new device while saving under the previous
    device's group -- writing into the wrong .storage file.
    """
    handler = re.search(r'closest\("\.device-card"\).*?\n  \}\);', js, re.DOTALL)
    assert handler, "no se encontró el handler de selección de dispositivo"
    assert "exitWizard()" in handler.group(0)


def test_a_device_that_cannot_learn_hides_the_wizard_panel(js):
    """Otherwise a grid of buttons is shown over a device that only errors."""
    render = re.search(r"function renderLearn\(\).*?\n\}", js, re.DOTALL).group(0)
    assert '$("#wizard-panel").classList.add("hidden")' in render
    assert "wizard = null" in render


def test_wizard_progress_comes_from_the_backend(js):
    """Read from .storage, not from the in-memory codes table.

    That table is only filled once the Códigos tab or a device has been opened,
    so relying on it showed every button as unlearned and had the user
    re-capture codes that already existed, overwriting them on save.
    """
    learned = re.search(r"function learnedCommands\(\).*?\n\}", js, re.DOTALL).group(0)
    assert "wizard.learned" in learned
    assert "codeGroups" not in learned
    assert "refreshWizardProgress" in js


def test_saving_follows_an_edited_group_name(js):
    """The group field stays editable, so the wizard has to track what was saved.

    Otherwise it keeps looking in the original group, shows the button as still
    missing, and the user learns it twice into two different groups.
    """
    submit = re.search(r"async function submitSave.*?\n\}", js, re.DOTALL).group(0)
    assert "savedGroup" in submit
    assert "wizard.group = savedGroup" in submit


def test_the_ir_rf_choice_cannot_be_answered_by_dismissing(js):
    """A confirm dialog treated Esc as the second option and started a capture.

    Anything other than an explicit IR or RF now aborts.
    """
    capture = re.search(r"async function wizardCapture.*?\n\}", js, re.DOTALL).group(0)
    assert "window.confirm" not in capture
    assert 'normalized !== "ir"' in capture
