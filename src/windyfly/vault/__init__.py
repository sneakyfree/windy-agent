"""Windy Vault, agent side (Agent ACCESS strand, DARK).

Only the lease key and the lease decrypt live here until the minimal Vault v1 proves it needs more
(Boss ruling 10-07: the egress guard, limiter, gateway frame and by-value redactor were deleted).
Nothing here is registered as a tool or called by the agent loop.
"""
