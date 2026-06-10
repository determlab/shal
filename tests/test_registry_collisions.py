"""Driver-registry collision policy — no silent last-write-wins overwrite."""
import pytest

import shal
from shal import registry


def test_same_class_reregister_is_noop():
    class D(shal.Driver):
        compatible = "test,reg-noop"

    registry.register(D)
    registry.register(D)  # idempotent
    assert registry.resolve("test,reg-noop") is D


def test_distinct_classes_collide_loudly():
    class A(shal.Driver):
        compatible = "test,reg-clash"

    class B(shal.Driver):
        compatible = "test,reg-clash"

    registry.register(A)
    registry.register(B)
    with pytest.raises(shal.LoadError, match="claimed by 2 drivers"):
        registry.resolve("test,reg-clash")


def test_override_shadows_intentionally():
    class A(shal.Driver):
        compatible = "test,reg-override"

    class B(shal.Driver):
        compatible = "test,reg-override"

    registry.register(A)
    registry.register(B, override=True)  # I mean to win
    assert registry.resolve("test,reg-override") is B


def test_unknown_compatible_still_fails():
    with pytest.raises(shal.LoadError, match="no driver installed"):
        registry.resolve("nobody,here")
