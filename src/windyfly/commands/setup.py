"""Initialize all commands at startup."""


def init_all_commands(db=None, config=None):
    """Register all 140 commands."""
    from windyfly.commands.core import init_core
    from windyfly.commands.ecosystem import init_ecosystem

    init_core(db=db, config=config)
    init_ecosystem(db=db)

    from windyfly.commands.registry import registry
    core, eco = registry.count()
    print(f"  Commands registered: {core} core + {eco} ecosystem = {core + eco} total")
