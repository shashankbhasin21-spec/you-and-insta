import pytest

from qbrain.agents import Param
from qbrain.quantum import QuantumInspiredOptimizer, gray_to_int


def test_gray_code_decoding():
    gray = {0: "000", 1: "001", 2: "011", 3: "010", 4: "110", 5: "111", 6: "101", 7: "100"}
    for value, code in gray.items():
        assert gray_to_int([int(b) for b in code]) == value


def _bowl(p):
    return -((p["x"] - 3.0) ** 2) - (p["y"] + 1.0) ** 2


def test_finds_the_top_of_a_bowl():
    space = [Param("x", -10, 10), Param("y", -10, 10)]
    result = QuantumInspiredOptimizer(space, _bowl, population=20, generations=60, bits_per_param=8, seed=3).run()
    assert result.best_params["x"] == pytest.approx(3.0, abs=0.5)
    assert result.best_params["y"] == pytest.approx(-1.0, abs=0.5)
    assert result.history == sorted(result.history)  # the best never gets worse


def test_is_deterministic_for_a_seed():
    space = [Param("x", -10, 10), Param("y", -10, 10)]
    a = QuantumInspiredOptimizer(space, _bowl, generations=10, seed=9).run()
    b = QuantumInspiredOptimizer(space, _bowl, generations=10, seed=9).run()
    assert a.best_params == b.best_params


def test_integer_params_and_bad_fitness_values():
    space = [Param("n", 1, 50, integer=True)]
    result = QuantumInspiredOptimizer(space, lambda p: float("nan") if p["n"] < 10 else -abs(p["n"] - 20),
                                      generations=20, seed=0).run()
    assert result.best_params["n"] == pytest.approx(20, abs=2)
    assert float(result.best_params["n"]).is_integer()
