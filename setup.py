from setuptools import setup

def get_version():
    with open("arcompile_version.py", encoding="utf-8") as f:
        for line in f:
            if line.startswith("__version__"):
                delim = '"' if '"' in line else "'"
                return line.split(delim)[1]
    raise RuntimeError("No se pudo encontrar la versión.")
        
setup(
    name='arcompile',
    version=get_version(),
    py_modules=['arcompile'],  # 👈 NO 'src.arcompile' ni nada raro
    install_requires=[],
    entry_points={
        'console_scripts': [
            'arcompile = arcompile:main'  # 👈 main() debe existir en arcompile.py
        ],
    },
)
