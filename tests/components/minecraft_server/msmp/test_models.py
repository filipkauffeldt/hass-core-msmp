"""Tests for the MSMP models."""

import pytest

from homeassistant.components.minecraft_server.msmp.models import (
    MsmpGameMode,
    MsmpGameModeName,
)


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
