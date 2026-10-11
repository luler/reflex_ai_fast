import reflex as rx
from reflex.plugins.sitemap import SitemapPlugin

config = rx.Config(
    app_name="image_gen_page",
    disable_plugins=[
        SitemapPlugin,
    ],
)

