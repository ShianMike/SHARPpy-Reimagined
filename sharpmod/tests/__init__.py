"""SHARPpy Reimagined test suite.

Hosts the shared Hypothesis :func:`~sharpmod.tests.strategies.profiles` strategy
and the property-based / unit tests that validate the SharpTab derived-parameter
library, the data-source decoders, and the viz widgets.

Other test modules obtain the generator with::

    from sharpmod.tests.strategies import profiles

The shared Hypothesis settings profile (minimum 100 examples) is registered and
loaded as the default in :mod:`sharpmod.tests.conftest`.
"""
