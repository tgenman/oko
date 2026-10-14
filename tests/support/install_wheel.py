"""Install a wheel into an isolated library directory, honoring wheel RECORD."""
from pathlib import Path
import sys
from installer import install
from installer.destinations import SchemeDictionaryDestination
from installer.sources import WheelFile


def install_wheel(wheel: Path, target: Path) -> None:
    destination = SchemeDictionaryDestination(
        scheme_dict={name: str(target / name) for name in ['scripts', 'headers', 'data']} | {'purelib':str(target), 'platlib':str(target)},
        interpreter=sys.executable, script_kind='posix', bytecode_optimization_levels=[],
    )
    with WheelFile.open(wheel) as source:
        install(source, destination, additional_metadata={'INSTALLER':b'oko-test-installer'})


if __name__ == '__main__':
    install_wheel(Path(sys.argv[1]), Path(sys.argv[2]))
