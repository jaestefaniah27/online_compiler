from setuptools import setup

setup(
    name='arcompile',
    version='0.1.0',
    py_modules=['arcompile'],  # 👈 NO 'src.arcompile' ni nada raro
    install_requires=[],
    entry_points={
        'console_scripts': [
            'arcompile = arcompile:main'  # 👈 main() debe existir en arcompile.py
        ],
    },
)
