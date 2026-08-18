from aiohttp.web import Application

from uflow import setup


def test_setup_does_not_duplicate_route_registrar():
    app = Application()

    setup(app)

    assert len(list(app.router.routes())) == 0
