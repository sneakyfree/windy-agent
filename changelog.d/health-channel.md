- HQ telemetry: `service.boot` and `service.health` now carry `channel` (the transport NAME, e.g. "telegram" or
  "matrix"; never a chat, room or user id), so HQ can alarm when ONE channel goes silent while the other keeps
  beating (Admin 10-29). Before, `start()` was called with no channel, so `channel_count` was never set either.
