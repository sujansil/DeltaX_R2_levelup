from pathlib import Path


def test_player_timer_expiry_refreshes_state_and_returns_cleanly():
    source = (Path(__file__).parents[1] / "static" / "play.html").read_text()

    assert "sreturn;" not in source
    assert """if (computeRemainingSeconds(state) <= 0) {
          timerAnimationFrame = null;
          refreshState();
          return;
        }""" in source
