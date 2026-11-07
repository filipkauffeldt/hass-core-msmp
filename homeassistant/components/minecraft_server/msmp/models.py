"""Typed results of MSMP methods.

This module must not import anything from Home Assistant.
"""

from dataclasses import dataclass
from typing import Any, Self


@dataclass
class MsmpPlayer:
    """A player on the server."""

    id: str
    name: str

    @classmethod
    def from_result(cls, result: dict[str, Any]) -> Self:
        """Build a player from its JSON representation."""
        return cls(id=result["id"], name=result["name"])


@dataclass
class MsmpServerStatus:
    """The state of the server, as returned by `minecraft:server/status`."""

    started: bool
    version_name: str
    protocol_version: int
    players: list[MsmpPlayer]

    @classmethod
    def from_result(cls, result: dict[str, Any]) -> Self:
        """Build the status from the method's result."""
        return cls(
            started=result["started"],
            version_name=result["version"]["name"],
            protocol_version=result["version"]["protocol"],
            players=[MsmpPlayer.from_result(player) for player in result["players"]],
        )
