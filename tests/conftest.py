import pytest

from houda_leads import config, rules


@pytest.fixture(scope="session")
def cfg():
    return config.load()


@pytest.fixture(scope="session")
def R(cfg):
    return rules.load(config.ROOT / "inputs", cfg)
