"""An actual second process and concurrent runtime threads participate in maintenance."""

import subprocess
import sys
import threading
import time
import pytest
from quality.maintenance import gate, MaintenanceBusy


def test_thread_and_process_writers_wait_for_existing_readers_and_nested_work(tmp_path):
    entered, released = threading.Event(), threading.Event()

    def writer():
        with gate(tmp_path, exclusive=True, timeout=5):
            entered.set()
            released.wait(5)

    # A second Python process holds the same OS read lock until released via stdin.
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from quality.maintenance import gate; from pathlib import Path; "
            'g=gate(Path(sys.argv[1])); g.__enter__(); print("locked", flush=True); sys.stdin.readline(); g.__exit__(None,None,None)',
            str(tmp_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout.readline().strip() == "locked"
    with gate(tmp_path):
        with gate(tmp_path):
            with pytest.raises(MaintenanceBusy):
                with gate(tmp_path, exclusive=True, timeout=0.1):
                    pass
        thread = threading.Thread(target=writer)
        thread.start()
        assert not entered.wait(0.1)
    assert not entered.wait(0.1)  # local reader ended; external process still owns a read lock
    process.stdin.write("release\n")
    process.stdin.flush()
    assert process.wait(timeout=5) == 0
    assert entered.wait(5)
    with pytest.raises(MaintenanceBusy):
        with gate(tmp_path, timeout=0.1):
            pass
    released.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    with gate(tmp_path):
        assert time.monotonic() > 0
