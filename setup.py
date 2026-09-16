from setuptools import Extension, setup


setup(
    ext_modules=[
        Extension(
            "_gospace_native",
            sources=["native/_gospace_native.c"],
            libraries=["dl"],
            extra_compile_args=["-O3"],
        )
    ]
)
