"""Scratch script to try the MSMP client against our real server.

Runs the client standalone, outside Home Assistant. The client code lives in
the integration, but nothing in the integration uses it yet: no config flow,
no setup in __init__.py, no coordinator, no entities, events or services.

It's obviously temporary, we'll remove it when these are setup too.

Run this from the root of the core repo with the venv active:

-> Open a terminal and go to the root of your repo (e.g. cd ~/src/hass-core-msmp)
-> Run this command as a standalone script:

MSMP_HOST=<MSMP_HOST> MSMP_PORT=<MSMP_PORT> MSMP_TOKEN=<MSMP_TOKEN> python -m homeassistant.components.minecraft_server.msmp.temp.scratch

Don't forget to fill the environment variables:
    MSMP_HOST   Server host (ours starts with mc)
    MSMP_PORT   Management server port
    MSMP_TOKEN  Your-God-given-token (the Bearer token)
    MSMP_TLS    Set to 1 to connect with wss://

Expected output on success:
    connected: True
    after close: False
"""

import asyncio
import os

import aiohttp

from ..client import MsmpClient


async def main() -> None:
    """Connect, report the state, then close."""
    async with aiohttp.ClientSession() as session:
        client = MsmpClient(
            session,
            os.environ["MSMP_HOST"],
            int(os.environ["MSMP_PORT"]),
            os.environ["MSMP_TOKEN"],
            tls=os.environ.get("MSMP_TLS") == "1",
        )
        async with client:
            print("connected:", client.connected)  # noqa: T201
        print("after close:", client.connected)  # noqa: T201
        print("status:", await client.call("minecraft:server/status"))  # noqa: T201


asyncio.run(main())
