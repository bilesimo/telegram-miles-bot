import io
import json
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import miles_transfer_bot


class ParseRssItemsTests(unittest.TestCase):
    def test_parse_rss_item_strips_html_and_parses_datetime(self) -> None:
        xml_text = textwrap.dedent(
            """\
            <?xml version="1.0" encoding="UTF-8"?>
            <rss version="2.0">
              <channel>
                <item>
                  <title><![CDATA[ LATAM <b>transfer</b> bonus ]]></title>
                  <link>https://example.com/promo</link>
                  <description><![CDATA[Up to <strong>30%</strong> bonus]]></description>
                  <pubDate>Fri, 08 May 2026 12:00:00 GMT</pubDate>
                </item>
              </channel>
            </rss>
            """
        )

        items = miles_transfer_bot.parse_rss_items(xml_text, "Fixture")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "LATAM transfer bonus")
        self.assertEqual(items[0].summary, "Up to 30% bonus")
        self.assertEqual(items[0].source_name, "Fixture")
        self.assertEqual(items[0].published.isoformat(), "2026-05-08T12:00:00+00:00")

    def test_parse_atom_item_uses_alternate_link(self) -> None:
        xml_text = textwrap.dedent(
            """\
            <?xml version="1.0" encoding="UTF-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <title>Azul transfer bonus</title>
                <summary>Livelo with 80% bonus</summary>
                <updated>2026-05-08T12:00:00Z</updated>
                <link rel="self" href="https://example.com/self" />
                <link rel="alternate" href="https://example.com/atom-promo" />
              </entry>
            </feed>
            """
        )

        items = miles_transfer_bot.parse_rss_items(xml_text, "Fixture")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].link, "https://example.com/atom-promo")
        self.assertEqual(items[0].title, "Azul transfer bonus")

    def test_parse_html_items_keeps_only_matching_article_links(self) -> None:
        html_text = textwrap.dedent(
            """\
            <html>
              <body>
                <a href="/categorias/promocoes/">Promoções</a>
                <a href="https://www.melhoresdestinos.com.br/milhas/livelo-azul-bonus-mai26">
                  Azul Fidelidade oferece até 110% de bônus na transferência de pontos Livelo
                </a>
                <a href="https://www.melhoresdestinos.com.br/passagens/some-flight-deal">
                  Oferta de voo
                </a>
              </body>
            </html>
            """
        )

        items = miles_transfer_bot.parse_html_items(
            html_text,
            {
                "name": "Melhores Destinos - Milhas",
                "url": "https://www.melhoresdestinos.com.br/milhas",
                "kind": "html",
                "link_include_patterns": [
                    r"https://www\.melhoresdestinos\.com\.br/milhas/.+"
                ],
                "minimum_title_length": 24,
            },
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0].link,
            "https://www.melhoresdestinos.com.br/milhas/livelo-azul-bonus-mai26",
        )
        self.assertIn("Azul Fidelidade", items[0].title)

    def test_parse_html_items_can_strip_author_and_relative_time_from_title(self) -> None:
        html_text = textwrap.dedent(
            """\
            <html>
              <body>
                <a href="https://passageirodeprimeira.com/promo">
                  LATAM Pass oferece 25% de bônus na transferência de pontos do Banrisul Roberto Bodetti há 2 dias
                </a>
              </body>
            </html>
            """
        )

        items = miles_transfer_bot.parse_html_items(
            html_text,
            {
                "name": "PP",
                "url": "https://passageirodeprimeira.com/categorias/promocoes/transferencia-de-pontos/",
                "kind": "html",
                "link_include_patterns": [r"https://passageirodeprimeira\.com/.+"],
                "title_cleanup_patterns": [
                    r"\s+(?:Jessika Dantas|Roberto Bodetti|Ana Zacaron|Ana Beatriz Muzzi|Priscila Brisighello|Jonny Farias)\s+h[áa]\s+\d+\s+(?:dia|dias|hora|horas|minuto|minutos)$"
                ],
                "minimum_title_length": 24,
            },
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(
            items[0].title,
            "LATAM Pass oferece 25% de bônus na transferência de pontos do Banrisul",
        )

    def test_extract_article_published_parses_brazilian_datetime_text(self) -> None:
        article_html = textwrap.dedent(
            """\
            <html>
              <body>
                <p>Publicado em 22/04/2026 às 10:37</p>
              </body>
            </html>
            """
        )

        published = miles_transfer_bot.extract_article_published(article_html)

        self.assertIsNotNone(published)
        self.assertEqual(published.isoformat(), "2026-04-22T10:37:00-03:00")


class FilteringTests(unittest.TestCase):
    def test_is_transfer_promo_requires_all_keyword_groups(self) -> None:
        item = miles_transfer_bot.FeedItem(
            source_name="Fixture",
            title="Azul Fidelidade with 90% Bonus",
            link="https://example.com/azul-livelo-transfer",
            summary="Transfer your Livelo points today.",
            published=None,
        )

        self.assertTrue(
            miles_transfer_bot.is_transfer_promo(
                item,
                tracked_terms=["latam", "azul", "livelo"],
                transfer_terms=["transfer"],
                bonus_terms=["bonus", "bônus"],
                negative_terms=[],
            )
        )
        self.assertFalse(
            miles_transfer_bot.is_transfer_promo(
                item,
                tracked_terms=["latam"],
                transfer_terms=["transfer"],
                bonus_terms=["bonus", "bônus"],
                negative_terms=[],
            )
        )

    def test_is_transfer_promo_rejects_crediting_status_posts(self) -> None:
        item = miles_transfer_bot.FeedItem(
            source_name="Fixture",
            title="LATAM Pass começa a creditar o bônus de transferência da campanha com a Livelo",
            link="https://example.com/status-post",
            summary="A campanha anterior com a Livelo já começou a ser creditada.",
            published=None,
        )

        self.assertFalse(
            miles_transfer_bot.is_transfer_promo(
                item,
                tracked_terms=["latam", "livelo"],
                transfer_terms=["transfer", "transferência"],
                bonus_terms=["bonus", "bônus"],
                negative_terms=["começa a creditar", "bônus de transferência da campanha"],
            )
        )

    def test_dedupe_items_prefers_richer_feed_entry_over_html_copy(self) -> None:
        html_item = miles_transfer_bot.FeedItem(
            source_name="HTML",
            title="LATAM Pass oferece 25% de bônus",
            link="https://example.com/promo",
            summary="",
            published=None,
        )
        feed_item = miles_transfer_bot.FeedItem(
            source_name="Feed",
            title="LATAM Pass oferece 25% de bônus na transferência de pontos do Banrisul",
            link="https://example.com/promo",
            summary="Resumo mais completo",
            published=miles_transfer_bot.parse_datetime("Fri, 08 May 2026 12:00:00 GMT"),
        )

        items = miles_transfer_bot.dedupe_items([html_item, feed_item])

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source_name, "Feed")


class OfficialConfirmationTests(unittest.TestCase):
    def test_confirm_official_links_returns_verified_matches(self) -> None:
        article_html = textwrap.dedent(
            """\
            <html>
              <body>
                <time datetime="2026-05-08T09:30:00-03:00"></time>
                <a href="https://latampass.latam.com/pt_br/promocao/livelo-pontos-extras">
                  Página oficial
                </a>
              </body>
            </html>
            """
        )
        official_html = """
            <html><body>Transfera seus pontos Livelo e ganhe 25% de bônus.</body></html>
        """

        def fake_fetch_text(url: str, timeout_seconds: int) -> str:
            if url == "https://example.com/article":
                return article_html
            if url == "https://latampass.latam.com/pt_br/promocao/livelo-pontos-extras":
                return official_html
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(miles_transfer_bot, "fetch_text", side_effect=fake_fetch_text):
            published, links = miles_transfer_bot.confirm_official_links(
                "https://example.com/article",
                timeout_seconds=5,
                official_link_patterns=[
                    r"^https://(?:www\.)?latampass\.latam\.com/"
                ],
                transfer_terms=["transfer", "transfira"],
                bonus_terms=["bonus", "bônus"],
            )

        self.assertEqual(published.isoformat(), "2026-05-08T09:30:00-03:00")
        self.assertEqual(
            links,
            ["https://latampass.latam.com/pt_br/promocao/livelo-pontos-extras"],
        )

    def test_format_message_includes_official_page_when_available(self) -> None:
        item = miles_transfer_bot.FeedItem(
            source_name="Fixture",
            title="LATAM transfer bonus 30% from Livelo",
            link="https://example.com/promo-1",
            summary="Great transfer bonus available now.",
            published=None,
            official_links=[
                "https://latampass.latam.com/pt_br/promocao/livelo-pontos-extras"
            ],
        )

        message = miles_transfer_bot.format_message(item)

        self.assertIn("<b>Miles transfer promo found</b>", message)
        self.assertIn("<b>Bonus:</b> 30%", message)
        self.assertIn(
            '<a href="https://latampass.latam.com/pt_br/promocao/livelo-pontos-extras">Official page</a>',
            message,
        )
        self.assertIn('<a href="https://example.com/promo-1">Article</a>', message)


class RunOnceTests(unittest.TestCase):
    def test_run_once_dry_run_dedupes_and_persists_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            feed_path = tmp_path / "feed.xml"
            article_path = tmp_path / "article.html"
            official_path = tmp_path / "official.html"
            config_path = tmp_path / "config.json"
            state_path = tmp_path / "state.json"

            official_path.write_text(
                "<html><body>Transfira seus pontos e ganhe 30% de bonus.</body></html>",
                encoding="utf-8",
            )
            article_path.write_text(
                (
                    "<html><body>"
                    f'<a href="{official_path.resolve().as_uri()}">Pagina oficial</a>'
                    "</body></html>"
                ),
                encoding="utf-8",
            )

            feed_path.write_text(
                textwrap.dedent(
                    """\
                    <?xml version="1.0" encoding="UTF-8"?>
                    <rss version="2.0">
                      <channel>
                        <item>
                          <title>LATAM transfer bonus 30% from Livelo</title>
                          <link>{article_link}</link>
                          <description>Great transfer bonus available now.</description>
                          <pubDate>Fri, 08 May 2026 12:00:00 GMT</pubDate>
                        </item>
                        <item>
                          <title>Unrelated post</title>
                          <link>https://example.com/other</link>
                          <description>No transfer here.</description>
                          <pubDate>Fri, 08 May 2026 11:00:00 GMT</pubDate>
                        </item>
                      </channel>
                    </rss>
                    """
                ).format(article_link=article_path.resolve().as_uri()),
                encoding="utf-8",
            )

            config_path.write_text(
                json.dumps(
                    {
                        "timeout_seconds": 5,
                        "tracked_terms": ["latam", "azul", "livelo"],
                        "transfer_terms": ["transfer", "transfira"],
                        "bonus_terms": ["bonus"],
                        "official_link_patterns": [r"^file://.*/official\.html$"],
                        "sources": [
                            {
                                "name": "Fixture",
                                "url": feed_path.resolve().as_uri(),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            first_output = io.StringIO()
            with redirect_stdout(first_output):
                exit_code = miles_transfer_bot.run_once(
                    config_path,
                    state_path,
                    dry_run=True,
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("<b>Miles transfer promo found</b>", first_output.getvalue())
            self.assertIn("<b>Bonus:</b> 30%", first_output.getvalue())
            self.assertIn("Official page</a>", first_output.getvalue())

            second_output = io.StringIO()
            with redirect_stdout(second_output):
                exit_code = miles_transfer_bot.run_once(
                    config_path,
                    state_path,
                    dry_run=True,
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("No new matching promos found.", second_output.getvalue())

            saved_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(saved_state["seen_ids"]), 1)

    def test_run_once_skips_unseen_stale_html_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            config_path = tmp_path / "config.json"
            state_path = tmp_path / "state.json"

            config_path.write_text(
                json.dumps(
                    {
                        "timeout_seconds": 5,
                        "tracked_terms": ["latam", "azul", "livelo"],
                        "transfer_terms": ["transfer", "transfira", "transferência"],
                        "bonus_terms": ["bonus", "bônus"],
                        "official_link_patterns": [
                            r"^https://(?:www\.)?latampass\.latam\.com/"
                        ],
                        "max_item_age_days": 7,
                        "sources": [],
                    }
                ),
                encoding="utf-8",
            )

            stale_item = miles_transfer_bot.FeedItem(
                source_name="Melhores Destinos - Milhas",
                title="LATAM Pass oferece 25% de bônus na transferência de pontos Esfera",
                link="https://www.melhoresdestinos.com.br/milhas/esfera-latam-pass-abr26",
                summary="",
                published=None,
            )
            article_html = textwrap.dedent(
                """\
                <html>
                  <body>
                    <p>Publicado em 22/04/2020 às 10:37</p>
                    <a href="https://latampass.latam.com/pt_br/promocao/esfera-milhas-extras">
                      Página oficial
                    </a>
                  </body>
                </html>
                """
            )
            official_html = """
                <html><body>Transfira seus pontos Esfera e ganhe 25% de bônus.</body></html>
            """

            def fake_fetch_text(url: str, timeout_seconds: int) -> str:
                if url == stale_item.link:
                    return article_html
                if url == "https://latampass.latam.com/pt_br/promocao/esfera-milhas-extras":
                    return official_html
                raise AssertionError(f"Unexpected URL: {url}")

            output = io.StringIO()
            with (
                patch.object(miles_transfer_bot, "fetch_matching_items", return_value=[stale_item]),
                patch.object(miles_transfer_bot, "fetch_text", side_effect=fake_fetch_text),
                redirect_stdout(output),
            ):
                exit_code = miles_transfer_bot.run_once(
                    config_path,
                    state_path,
                    dry_run=True,
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("Skipping stale promo", output.getvalue())
            self.assertIn("No new matching promos found.", output.getvalue())
            self.assertNotIn("<b>Miles transfer promo found</b>", output.getvalue())

            saved_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_state["seen_ids"], [stale_item.stable_id])


if __name__ == "__main__":
    unittest.main()
