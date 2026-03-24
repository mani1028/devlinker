from pathlib import Path

from setuptools import find_packages, setup

from onelink import __version__

README = Path(__file__).with_name("README.md").read_text(encoding="utf-8")

setup(
    name="onelink",
    version=__version__,
    author="OneLink Contributors",
    description="OneLink - Share full-stack apps instantly with one command",
    long_description=README,
    long_description_content_type="text/markdown",
    url="https://github.com/yourrepo/onelink",
    license="MIT",
    python_requires=">=3.8",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Operating System :: OS Independent",
        "Environment :: Console",
        "Topic :: Software Development :: Build Tools",
    ],
    packages=find_packages(),
    install_requires=["flask", "requests", "pyngrok", "click"],
    entry_points={"console_scripts": ["onelink=onelink.main:cli"]},
)
