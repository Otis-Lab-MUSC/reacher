import os
from setuptools import setup, find_packages

INSTALL_REQUIRES = [
    "pyserial>=3.5",      # For serial communication in core/reacher.py
    "panel>=1.0.0",       # For dashboard interfaces in wired_controls/
    "pandas>=2.0.0",      # For data handling in dashboards
    "plotly>=5.0.0",      # For plotting in dashboards
    "matplotlib>=3.5.0",  # For plotting square waves in dashboards
    "numpy>=1.22.0",      # For numerical operations in dashboards
]

setup(
    name="reacher",
    version="1.0.1",
    packages=find_packages(where="src"),  # Finds reacher and submodules
    package_dir={"": "src"},              # Root package is in src/
    install_requires=INSTALL_REQUIRES,
    package_data={
        "reacher": ["assets/*"],          # Include assets directory
    },
    author="Joshua Boquiren",
    author_email="thejoshbq@proton.me",
    description="A package necessary to run the REACHER Suite protocols.",
    long_description=open("README.md", encoding="utf-8").read() if os.path.exists("README.md") else "",
    long_description_content_type="text/markdown",
    url="https://github.com/Otis-Lab-MUSC/REACHER",
    license="MIT",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",  # Add if tested
    ],
    python_requires=">=3.8",
)