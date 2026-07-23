from odrive_4wd_controller.safety import CommandWatchdog, DriveState, SafetyMachine


def test_command_timeout():
    watchdog = CommandWatchdog(0.3)
    watchdog.feed(1.0)
    assert not watchdog.stale(1.3)
    assert watchdog.stale(1.30001)


def test_nonrecoverable_fault_cannot_be_cleared():
    machine = SafetyMachine(DriveState.ENABLED)
    machine.trip("USB_LOSS", "controller disconnected", 1.0)
    assert machine.state == DriveState.FAULT
    assert not machine.clear_recoverable()


def test_recoverable_fault_requires_explicit_clear():
    machine = SafetyMachine(DriveState.READY)
    machine.trip("STALE", "command stale", 1.0, recoverable=True)
    assert machine.clear_recoverable()
    assert machine.state == DriveState.IDLE
