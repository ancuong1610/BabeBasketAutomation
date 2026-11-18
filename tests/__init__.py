import os

# Tests never call real services; code that reads a secret just needs it to exist.
for _name in ("OPENROUTER_API_KEY", "DISCORD_BOT_TOKEN", "THREADS_ACCESS_TOKEN"):
    os.environ.setdefault(_name, "test-dummy")
