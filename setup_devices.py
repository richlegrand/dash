import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="dash_devices", # Replace with your own username
    version="0.1.3",
    author="Charmed Labs",
    author_email="support@charmedlabs.com",
    description="Dash for devices.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/richlegrand/dash_devices",
    packages=setuptools.find_packages(),
    include_package_data=True,
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.7',
)