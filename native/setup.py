from setuptools import Extension, setup

setup(
    name="pyspace-gospace-native",
    version="0.1.0",
    ext_modules=[Extension("_gospace_native", ["_gospace_native.c"], libraries=["dl"])],
)
