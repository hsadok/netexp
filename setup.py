
import io
import re
from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as readme_file:
    long_description = readme_file.read()

name = 'netexp'


# Hack to avoid having to define metadata twice. Instead define it in the
# `__init__.py`` file. This is adapted from here:
# https://stackoverflow.com/a/39671214/2027390
def find_meta(meta_name):
    return re.search(
        meta_name
        + r'\s*=\s*[\'"]([^\'"]*)[\'"]',  # It excludes inline comment too
        io.open(f'{name}/__init__.py', encoding='utf_8_sig').read()
    ).group(1)


setup(
    name=name,
    version=find_meta('__version__'),
    description='Python library to automate network experiments.',
    long_description=long_description,
    long_description_content_type='text/markdown',
    packages=find_packages(),
    url='https://github.com/hsadok/netexp',
    download_url='https://github.com/hsadok/netexp',
    license='BSD',
    author=find_meta('__author__'),
    author_email=find_meta('__email__'),
    keywords=['network', 'experiment', 'automation'],
    python_requires='>=3.6',
    include_package_data=True,
    install_requires=[
        'paramiko',
        'cffi'  # required by paramiko
    ],
    setup_requires=[
        'pytest-runner'
    ],
    tests_require=[
        'pytest'
    ],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Science/Research',
        'License :: OSI Approved :: BSD License',
        'Natural Language :: English',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Topic :: Scientific/Engineering :: Visualization',
        'Topic :: System :: Installation/Setup ',
        'Topic :: System :: Networking',
        'Topic :: System :: Systems Administration',
    ],
)
