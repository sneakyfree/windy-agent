# Re-export legacy symbols so `from windyfly.commands import cmd_doctor` still works.
# New code should use `from windyfly.commands.registry import registry` instead.

# Import everything from the legacy module (renamed from commands.py)
from windyfly.commands._legacy import *  # noqa: F401,F403

# Also re-export private helpers used in tests
from windyfly.commands._legacy import (  # noqa: F401
    VERSION,
    _check_port,
    _config_path,
    _config_set,
    _config_show,
    _doc_row,
    _format_uptime,
    _get_db_path,
    _open_db,
)
