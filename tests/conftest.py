import pytest

from tests.fakes import FakeTransport


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport({"ATZ": "ELM327 v1.5", "ATRV": "12.6V", "03": "43 01 33 00 00 00 00"})
