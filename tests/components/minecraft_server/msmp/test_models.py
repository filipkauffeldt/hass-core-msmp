"""Tests for the MSMP models."""

import pytest

from homeassistant.components.minecraft_server.msmp.models import (
    MsmpGameMode,
    MsmpGameModeName,
    MsmpPlayer,
    MsmpServerStatus,
)

PLAYER_RESULT = {"id": "853c80ef-3c37-49fd-aa49-938b674adae6", "name": "jeb_"}
STATUS_RESULT = {
    "started": True,
    "version": {"name": "1.21.9", "protocol": 773},
    "players": [PLAYER_RESULT],
}


@pytest.mark.parametrize(
    ("result", "game_mode_name"),
    [
        pytest.param("survival", MsmpGameModeName.SURVIVAL, id="survival"),
        pytest.param("creative", MsmpGameModeName.CREATIVE, id="creative"),
        pytest.param("adventure", MsmpGameModeName.ADVENTURE, id="adventure"),
        pytest.param("spectator", MsmpGameModeName.SPECTATOR, id="spectator"),
    ],
)
def test_game_mode_from_result(result: str, game_mode_name: MsmpGameModeName) -> None:
    """Test each game mode the protocol defines is accepted."""
    assert MsmpGameMode.from_result(result) == MsmpGameMode(game_mode=game_mode_name)


@pytest.mark.parametrize(
    "result",
    [
        pytest.param("hardcore", id="unknown-game-mode"),
        pytest.param("Survival", id="wrong-case"),
        pytest.param("", id="empty-string"),
        pytest.param(1, id="number"),
        pytest.param(["survival"], id="list"),
        pytest.param({"mode": "survival"}, id="object"),
        pytest.param(None, id="none"),
    ],
)
def test_game_mode_from_result_invalid(
    result: str | int | list[str] | dict[str, str] | None,
) -> None:
    """Test everything except the protocol's game modes is rejected."""
    with pytest.raises(ValueError, match="is not a valid MsmpGameModeName"):
        MsmpGameMode.from_result(result)


def test_server_status_from_result() -> None:
    """Test the nested status result is flattened into a typed status."""
    assert MsmpServerStatus.from_result(STATUS_RESULT) == MsmpServerStatus(
        started=True,
        version_name="1.21.9",
        protocol_version=773,
        players=[MsmpPlayer(id=PLAYER_RESULT["id"], name="jeb_")],
    )


def test_server_status_from_result_empty_players() -> None:
    """Test an empty players list is an empty server, not an error."""
    status = MsmpServerStatus.from_result({**STATUS_RESULT, "players": []})

    assert status.players == []


def test_server_status_from_result_missing_players() -> None:
    """Test a status without a players key means no players are online."""
    result = {"started": True, "version": {"name": "26.3", "protocol": 777}}

    assert MsmpServerStatus.from_result(result) == MsmpServerStatus(
        started=True, version_name="26.3", protocol_version=777, players=[]
    )


def test_server_status_from_result_no_mutation() -> None:
    """Test building a status leaves the server's result untouched."""
    result = {"started": True, "version": {"name": "26.3", "protocol": 777}}

    MsmpServerStatus.from_result(result)

    assert "players" not in result
