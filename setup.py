from setuptools import setup
from arcompile_version import __version__ as VERSION
def get_version():
    with open("version.txt", "r", encoding="utf-8") as f:
        return f.read().strip()
        
setup(
    name='arcompile',
    version=VERSION,
    py_modules=['arcompile'],  # 👈 NO 'src.arcompile' ni nada raro
    install_requires=[],
    entry_points={
        'console_scripts': [
            'arcompile = arcompile:main'  # 👈 main() debe existir en arcompile.py
        ],
    },
)
