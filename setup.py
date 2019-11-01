#!/usr/bin/env python
# -*- coding: utf-8 -*-
try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup


requires = [
    "urllib3>=1.7.1",
    'pure-python-adb>=0.2.2.dev'
]
test_requires = [
    'nose>=1.0',
    'mock>=1.0.1',
    'coverage>=3.6'
]

version = '1.0.0'

setup(
    name='ppadb-uiautomator',
    version=version,
    description='Fork from uiautomator - Python Wrapper for Android UiAutomator test tool',
    long_description='Fork from uiautomator - Python wrapper for Android uiautomator tool and replace the adb cli by ppadb.',
    author='Xiaocong He',
    author_email='xiaocong@gmail.com,hongbin.bao@gmail.com',
    url='https://github.com/swind/uiautomator',
    download_url='https://github.com/swind/uiautomator/tarball/%s' % version,
    keywords=[
        'testing', 'android', 'uiautomator'
    ],
    install_requires=requires,
    tests_require=test_requires,
    test_suite="nose.collector",
    packages=['uiautomator'],
    package_data={
        'uiautomator': [
            'uiautomator/libs/bundle.jar',
            'uiautomator/libs/uiautomator-stub.jar',
            'uiautomator/libs/app-uiautomator-test.apk',
            'uiautomator/libs/app-uiautomator.apk'
        ]
    },
    include_package_data=True,
    license='MIT',
    platforms='any',
    classifiers=(
        'Development Status :: 3 - Alpha',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'Operating System :: POSIX :: Linux',
        'Programming Language :: Python :: 3.2',
        'Programming Language :: Python :: 3.3',
        'Programming Language :: Python :: 3.4',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: Software Development :: Testing'
    )
)
