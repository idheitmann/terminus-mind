import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolated_index(tmp_path_factory):
    """Keep test embedding indexes out of the real cache directory."""
    os.environ["TM_INDEX"] = str(tmp_path_factory.mktemp("index"))
