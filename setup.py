from setuptools import setup, find_packages

setup(
    name='arcompile',
    version='0.1.0',
    packages=find_packages(),
    py_modules=['arcompile'],
    install_requires=[],
    entry_points={
        'console_scripts': [
            'arcompile = arcompile:main'
        ],
    },
    author='jaestefaniah27',
    description='Compilador remoto de sketches Arduino con ESP32 y flasheo local',
    url='https://github.com/jaestefaniah27/online_compiler',
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
    ],
    python_requires='>=3.6',
)
