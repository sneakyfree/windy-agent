"""Initialize all commands at startup."""

import logging

logger = logging.getLogger(__name__)


def init_all_commands(db=None, config=None):
    """Register all commands."""
    from windyfly.commands.core import init_core
    from windyfly.commands.ecosystem import init_ecosystem

    init_core(db=db, config=config)
    init_ecosystem(db=db)

    from windyfly.commands.registry import registry
    core, eco = registry.count()
    logger.info("Commands registered: %d core + %d ecosystem = %d total", core, eco, core + eco)

