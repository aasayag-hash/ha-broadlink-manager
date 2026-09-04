"""Tests for the IR/RF capture state machines.

A fake device stands in for the hardware so the timing-sensitive parts -- the
poll loop, the two RF phases, cancellation and the sweep cleanup -- can be
exercised without a remote in hand.
"""

from __future__ import annotations

import time

import broadlink.exceptions as blk_exc
import pytest

from backend import learning

CAPTURED_BYTES = b"\x26\x00\x50\x00captured"


class FakeRM:
    """Minimal stand-in for an rmpro.

    press_after models how long the user takes to press the button; before that
    check_data() raises ReadError, which is what a real RM pro does.
    """

    def __init__(self, press_after: float = 0.5, fail_phase: str | None = None):
        self.t0 = time.time()
        self.press_after = press_after
        self.fail_phase = fail_phase
        self.sweep_cancelled = False
        self.frequency_used: float | None = None
        # Distinguishes find_rf_packet() from find_rf_packet(None): passing an
        # explicit 0.0 is the bug this suite guards against.
        self.find_rf_args: list[tuple] = []
        self.learning_entered = False

    def _pressed(self) -> bool:
        return time.time() - self.t0 >= self.press_after

    def enter_learning(self) -> None:
        self.learning_entered = True

    def sweep_frequency(self) -> None:
        pass

    def check_frequency(self):
        if self.fail_phase == "sweep":
            return False, 0.0
        return self._pressed(), 433.92

    def find_rf_packet(self, *args) -> None:
        self.find_rf_args.append(args)
        self.frequency_used = args[0] if args else None

    def check_data(self) -> bytes:
        if self.fail_phase == "packet" or not self._pressed():
            raise blk_exc.ReadError(-10, "Read error")
        return CAPTURED_BYTES

    def cancel_sweep_frequency(self) -> None:
        self.sweep_cancelled = True


@pytest.fixture(autouse=True)
def short_timeouts(monkeypatch):
    """Keep the suite fast; the real values are 30s per phase."""
    monkeypatch.setattr(learning, "IR_TIMEOUT", 3.0)
    monkeypatch.setattr(learning, "SWEEP_TIMEOUT", 3.0)
    monkeypatch.setattr(learning, "PACKET_TIMEOUT", 3.0)
    monkeypatch.setattr(learning, "POLL_INTERVAL", 0.05)


def wait_until_done(session, timeout: float = 10.0):
    active = (learning.WAITING_IR, learning.SWEEPING, learning.WAITING_PACKET)
    deadline = time.time() + timeout
    while session.state in active and time.time() < deadline:
        time.sleep(0.05)
    return session


def fresh(mac: str):
    learning.clear(mac)
    return mac


# --- the poll loop ---------------------------------------------------------


def test_read_error_means_waiting_not_failure():
    """A real RM pro raises ReadError on every poll before a button is pressed.

    Treating that as an error ended the capture within a second of starting it,
    which is exactly what happened against real hardware.
    """
    mac = fresh("poll")
    device = FakeRM(press_after=1.0)
    session = learning.start(mac, device, "ir")

    time.sleep(0.4)
    assert session.state == learning.WAITING_IR  # still waiting, not failed

    wait_until_done(session)
    assert session.state == learning.CAPTURED


def test_storage_error_is_also_treated_as_waiting(monkeypatch):
    """Some firmwares answer -5 for the same "nothing captured yet" condition."""
    mac = fresh("storage-err")
    device = FakeRM(press_after=0.4)
    raised = {"count": 0}
    real_check = device.check_data

    def check_data():
        if raised["count"] < 2:
            raised["count"] += 1
            raise blk_exc.StorageError(-5, "The device storage is full")
        return real_check()

    device.check_data = check_data
    session = wait_until_done(learning.start(mac, device, "ir"))
    assert session.state == learning.CAPTURED


# --- IR --------------------------------------------------------------------


def test_ir_capture_encodes_base64():
    import base64

    mac = fresh("ir-ok")
    session = wait_until_done(learning.start(mac, FakeRM(0.2), "ir"))
    assert session.state == learning.CAPTURED
    assert base64.b64decode(session.code) == CAPTURED_BYTES


def test_ir_timeout_explains_what_to_check():
    mac = fresh("ir-timeout")
    session = wait_until_done(learning.start(mac, FakeRM(press_after=99), "ir"))
    assert session.state == learning.FAILED
    assert "pilas" in session.error


# --- RF --------------------------------------------------------------------


def test_rf_sweeps_then_captures():
    mac = fresh("rf-ok")
    device = FakeRM(0.2)
    session = wait_until_done(learning.start(mac, device, "rf"))
    assert session.state == learning.CAPTURED
    assert session.frequency == 433.92
    assert device.frequency_used == 433.92


def test_rf_can_skip_the_sweep_with_a_known_frequency():
    """Reusing the frequency is the difference between ~30s and ~3s per button.

    315 MHz works through the same path as 433: the frequency is just a number
    passed to find_rf_packet.
    """
    mac = fresh("rf-skip")
    device = FakeRM(0.2)
    session = learning.start(mac, device, "rf", frequency=315.1)

    time.sleep(0.15)
    assert session.state == learning.WAITING_PACKET  # never swept

    wait_until_done(session)
    assert device.frequency_used == 315.1
    assert session.frequency == 315.1


def test_a_device_that_hides_the_frequency_still_captures():
    """Measured on a real RM pro: it reports found=True with freq=0.0 always.

    That firmware never discloses the value. The capture must still work by
    calling find_rf_packet() with no argument -- the device kept the frequency
    internally. Passing the 0.0 through would leave the radio on no band and
    every capture would fail.
    """
    mac = fresh("rf-zero")

    class HidesFrequency(FakeRM):
        def check_frequency(self):
            return self._pressed(), 0.0

    device = HidesFrequency(press_after=0.2)
    session = wait_until_done(learning.start(mac, device, "rf"))

    assert session.state == learning.CAPTURED
    assert session.frequency is None  # nothing to remember for the next button
    assert device.find_rf_args == [()]  # called bare, not with 0.0
    # No "None MHz" anywhere the user can read it.
    assert "None" not in session.message


def test_a_stored_zero_frequency_is_refused():
    mac = fresh("rf-bad-freq")
    device = FakeRM(0.1)
    session = wait_until_done(learning.start(mac, device, "rf", frequency=0.0))
    assert session.state == learning.FAILED
    assert "barrido completo" in session.error
    assert device.frequency_used is None  # never reached the radio


def test_sweep_failure_says_to_hold_the_button():
    mac = fresh("rf-sweep-fail")
    session = wait_until_done(learning.start(mac, FakeRM(0.1, fail_phase="sweep"), "rf"))
    assert session.state == learning.FAILED
    assert "frecuencia" in session.error


def test_packet_failure_tells_the_user_not_to_sweep_again():
    """Losing the frequency after a successful sweep is the top forum complaint."""
    mac = fresh("rf-packet-fail")
    session = wait_until_done(learning.start(mac, FakeRM(0.1, fail_phase="packet"), "rf"))
    assert session.state == learning.FAILED
    assert "no hace falta repetir" in session.error


def test_sweep_is_always_cancelled():
    """Leaving the device in sweep mode wedges it until it is power cycled."""
    for phase in (None, "sweep", "packet"):
        mac = fresh(f"cleanup-{phase}")
        device = FakeRM(0.1, fail_phase=phase)
        wait_until_done(learning.start(mac, device, "rf"))
        assert device.sweep_cancelled, f"sweep not cancelled after phase={phase}"


def test_sweep_is_cancelled_on_user_cancel():
    mac = fresh("cleanup-cancel")
    device = FakeRM(press_after=99)
    session = learning.start(mac, device, "rf")
    time.sleep(0.2)
    learning.cancel(mac)
    wait_until_done(session)
    assert device.sweep_cancelled


# --- session handling ------------------------------------------------------


def test_cancel_stops_the_capture():
    mac = fresh("cancel")
    session = learning.start(mac, FakeRM(press_after=99), "ir")
    time.sleep(0.2)
    assert learning.cancel(mac)
    wait_until_done(session)
    assert session.state == learning.CANCELLED


def test_second_capture_on_the_same_device_is_refused():
    """The device has one learning mode; two captures would fight over it."""
    mac = fresh("dup")
    learning.start(mac, FakeRM(press_after=99), "ir")
    time.sleep(0.1)
    with pytest.raises(RuntimeError, match="captura en curso"):
        learning.start(mac, FakeRM(), "ir")
    learning.cancel(mac)


def test_a_finished_session_does_not_block_the_next_one():
    mac = fresh("reuse")
    wait_until_done(learning.start(mac, FakeRM(0.1), "ir"))
    session = wait_until_done(learning.start(mac, FakeRM(0.1), "ir"))
    assert session.state == learning.CAPTURED


def test_device_errors_are_reported_not_swallowed():
    mac = fresh("boom")

    class Broken(FakeRM):
        def enter_learning(self):
            raise blk_exc.AuthorizationError(-7, "Control key is expired")

    session = wait_until_done(learning.start(mac, Broken(), "ir"))
    assert session.state == learning.FAILED
    assert "Home Assistant" in session.error  # points at the usual cause


def test_packet_kind_is_read_from_the_code_not_the_requested_mode():
    """The first byte says how the code actually travels.

    Observed for real: an RF 433 remote pointed at an RM pro is captured by the
    IR flow and stored as 0xb2. Reporting the requested mode would have labelled
    that code "IR", which is simply wrong.
    """
    import base64

    assert learning.packet_kind(base64.b64encode(b"\x26\x00rest").decode()) == "IR"
    assert learning.packet_kind(base64.b64encode(b"\xb2\x07rest").decode()) == "RF 433"
    assert learning.packet_kind(base64.b64encode(b"\xd7\x00rest").decode()) == "RF 315"
    assert learning.packet_kind("no-es-base64-valido!!") is None
    assert learning.packet_kind("") is None


def test_captured_session_reports_its_kind():
    mac = fresh("kind")
    session = wait_until_done(learning.start(mac, FakeRM(0.2), "ir"))
    # FakeRM returns a 0x26 packet.
    assert session.as_dict()["kind"] == "IR"


def test_countdown_counts_down():
    mac = fresh("countdown")
    session = learning.start(mac, FakeRM(press_after=99), "ir")
    time.sleep(0.1)
    first = session.as_dict()["remaining"]
    time.sleep(1.1)
    assert session.as_dict()["remaining"] < first
    learning.cancel(mac)
