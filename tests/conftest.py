import pytest

def pytest_addoption(parser):
    parser.addoption("--run-real", action="store_true", default=False,
                     help="Run real dialog integration tests (opens windows)")

def pytest_configure(config):
    config.addinivalue_line("markers", "real: test that interacts with real windows")

def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-real"):
        return  # run all tests including real
    skip_real = pytest.mark.skip(reason="need --run-real option to run")
    for item in items:
        if "real" in item.keywords:
            item.add_marker(skip_real)
