"""Flet data layer: controllers and DTOs with ZERO widgets.

Everything under :mod:`flet_app.data` is UI-runtime free (``import flet`` is
forbidden here) so the controllers can be unit-tested and reused by ``flet-ui``
without a live Flet client. Widgets, layout, theme and copy belong to
``flet_app/`` (the ``flet-ui`` phase), never to this package.
"""
