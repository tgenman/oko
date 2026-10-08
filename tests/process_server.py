import os
from oko.__main__ import run


def fault(point: str) -> None:
    if point == os.environ.get('OKO_TEST_FAULT'):
        os._exit(79)


if __name__ == '__main__':
    run(fault=fault)
