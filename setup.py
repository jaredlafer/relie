from setuptools import setup, find_packages

setup(
    name='relie',
    version='1.14',
    packages=find_packages(),
    install_requires=[
        'torch',
        'numpy',
        'Pillow',
        'tensorboardX',
        'scikit-learn',
        'matplotlib'
    ]
)
