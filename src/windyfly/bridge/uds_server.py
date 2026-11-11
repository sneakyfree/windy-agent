"""Stand-in at the old address of the second process.

Until the lean Windy Fly (step R1) the agent ran as TWO processes: this module was the "brain" behind a local Bun
gateway on port 3000, and ``windy start`` and the units ``windy install-service`` wrote all started
``python -m windyfly.bridge.uds_server``. The bridge and the gateway are gone: the agent is ONE process on ONE
channel, Windy Chat.

A unit file written by an older version still names this module. So that such an install keeps starting after an
update, this stand-in says once what changed and then runs the one channel, exactly as
``python -m windyfly.main --channel matrix`` would. ``windy install-service`` writes the new command; this file can
go once no installed unit names it.
"""

from __future__ import annotations

import sys

ONE_CHANNEL = "matrix"
NOTICE = ("windyfly.bridge.uds_server is no longer a separate process: starting the one channel (Windy Chat) "
          "instead. Run `windy install-service` once to update this unit.\n")


def main() -> None:
    sys.stderr.write(NOTICE)
    sys.argv = [sys.argv[0], "--channel", ONE_CHANNEL]
    from windyfly.main import main as run_one_channel

    run_one_channel()


if __name__ == "__main__":
    main()
