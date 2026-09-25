from app.scoring import (
    apply_swaps_fifo,
    grade_answer,
    is_bottom_half,
    kingslayer_applies,
    points_for_outcome,
    potential_points_ms,
    ranks_by_score,
)


def test_no_decay_first_60s():
    assert potential_points_ms(0, False) == 2000
    assert potential_points_ms(60_000, False) == 2000
    assert potential_points_ms(60_000, True) == 6000


def test_decay_hits_minimum_at_135s():
    assert potential_points_ms(135_000, False) == 500
    assert potential_points_ms(150_000, False) == 500
    # 75s * 20 = 1500, 2000-1500 = 500
    assert potential_points_ms(134_000, False) == max(500, 2000 - 20 * 74)


def test_bounty_explicit_150_per_sec_and_min_1500():
    # 30s of decay * 150 = 4500 → 6000-4500 = 1500 at t=90s
    assert potential_points_ms(90_000, True) == 1500
    assert potential_points_ms(135_000, True) == 1500
    assert potential_points_ms(61_000, True) == 5850


def test_timeout_zero():
    assert (
        points_for_outcome(
            locked=False,
            has_answer=False,
            correct=None,
            bounty=True,
            freeze_elapsed_ms=None,
            locked_elapsed_ms=None,
            close_elapsed_ms=150_000,
            kingslayer=True,
        )
        == 0
    )


def test_wrong_penalty():
    assert (
        points_for_outcome(
            locked=True,
            has_answer=True,
            correct=False,
            bounty=False,
            freeze_elapsed_ms=None,
            locked_elapsed_ms=10_000,
            close_elapsed_ms=150_000,
            kingslayer=True,
        )
        == -500
    )
    assert (
        points_for_outcome(
            locked=True,
            has_answer=True,
            correct=False,
            bounty=True,
            freeze_elapsed_ms=None,
            locked_elapsed_ms=10_000,
            close_elapsed_ms=150_000,
            kingslayer=False,
        )
        == -1500
    )


def test_freeze_pauses_decay_during_and_after_freeze_window():
    # Freeze at 60s pauses the effective timer for 30s. While the freeze is active,
    # the player is still at the 60s value.
    assert points_for_outcome(
        locked=True,
        has_answer=True,
        correct=True,
        bounty=False,
        freeze_elapsed_ms=60_000,
        locked_elapsed_ms=75_000,
        close_elapsed_ms=75_000,
        kingslayer=False,
    ) == 2000

    # After the freeze ends, decay resumes from the 60s baseline and continues from there.
    assert points_for_outcome(
        locked=True,
        has_answer=True,
        correct=True,
        bounty=False,
        freeze_elapsed_ms=60_000,
        locked_elapsed_ms=120_000,
        close_elapsed_ms=120_000,
        kingslayer=False,
    ) == 1400

    # By 150s, the player is still decaying from the 60s baseline after the freeze has ended.
    assert points_for_outcome(
        locked=True,
        has_answer=True,
        correct=True,
        bounty=False,
        freeze_elapsed_ms=60_000,
        locked_elapsed_ms=150_000,
        close_elapsed_ms=150_000,
        kingslayer=False,
    ) == 800


def test_kingslayer_bonus():
    pts = points_for_outcome(
        locked=True,
        has_answer=True,
        correct=True,
        bounty=False,
        freeze_elapsed_ms=None,
        locked_elapsed_ms=10_000,
        close_elapsed_ms=10_000,
        kingslayer=True,
    )
    assert pts == 2500


def test_kingslayer_schedule_and_bottom_half_ties():
    assert not kingslayer_applies(5)
    assert kingslayer_applies(6)
    scores = {f"t{i}": 1000 - i for i in range(16)}
    ranks = ranks_by_score(scores)
    bottom = [tid for tid, r in ranks.items() if is_bottom_half(r, 16)]
    assert len(bottom) == 8
    # three-way tie such that rank is 8 — they are NOT bottom half
    tied = {
        "a": 10,
        "b": 9,
        "c": 8,
        "d": 8,
        "e": 8,
        "f": 1,
        "g": 1,
        "h": 1,
        "i": 1,
        "j": 1,
        "k": 1,
        "l": 1,
        "m": 1,
        "n": 1,
        "o": 1,
        "p": 1,
    }
    # 2 teams strictly above 8: a=10, b=9 so c,d,e have rank 3. Adjust:
    tied = {
        "p1": 100,
        "p2": 90,
        "p3": 80,
        "p4": 70,
        "p5": 60,
        "p6": 50,
        "p7": 40,
        "p8": 30,
        "p8b": 30,
        "p8c": 30,
        "low1": 5,
        "low2": 4,
        "low3": 3,
        "low4": 2,
        "low5": 1,
        "low6": 0,
    }
    r = ranks_by_score(tied)
    assert r["p8"] == 8 and r["p8b"] == 8 and r["p8c"] == 8
    assert not is_bottom_half(8, 16)
    assert is_bottom_half(r["low1"], 16)


def test_grade_types():
    assert grade_answer("mcq_single", {"option_id": "b"}, {"option_id": "b"})
    assert not grade_answer("mcq_single", {"option_id": "b"}, {"option_id": "a"})
    assert grade_answer("mcq_multi", {"option_ids": ["b", "c"]}, {"option_ids": ["c", "b"]})
    assert not grade_answer("mcq_multi", {"option_ids": ["b", "c"]}, {"option_ids": ["b"]})
    assert grade_answer("numeric", {"value": "3.14", "tolerance": "0.01"}, {"value": "3.141"})
    assert not grade_answer("numeric", {"value": "3.14", "tolerance": "0"}, {"value": "3.141"})
    assert grade_answer("text", {"value": "Texas Instruments"}, {"value": "  texas   instruments "})


def test_swap_fifo_and_empty_target():
    answers = {"A": {"v": 1}, "B": None, "C": {"v": 3}}
    locked = {"A": True, "B": False, "C": True}
    a2, l2, applied = apply_swaps_fifo(answers, locked, [("A", "B")])
    assert applied == [("A", "B")]
    assert a2["A"] is None
    assert a2["B"] == {"v": 1}
    assert l2["A"] is True
    assert l2["B"] is True
    a3, _, _ = apply_swaps_fifo(a2, l2, [("C", "B")])
    assert a3["B"] == {"v": 3}
    assert a3["C"] == {"v": 1}
