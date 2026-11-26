import pytest

from reddit_rss_fetcher.text import excerpt, holds_all, paragraphs, plain_paragraphs


@pytest.mark.parametrize(
    ("html", "text"),
    [
        ("<p>One</p><p>Two</p>", "One\n\nTwo"),
        ("One<br>Two<br/>Three", "One\n\nTwo\n\nThree"),
        ("<ul><li>a</li><li>b</li></ul>", "a\n\nb"),
        ("<div><div>Nested</div> <p>  spaced   out </p></div>", "Nested\n\nspaced out"),
        ('<div class="md"><p>Reddit &amp; its <a href="x">links</a></p></div>', "Reddit & its links"),
        ("<p></p><p> </p>", ""),
    ],
)
def test_plain_paragraphs(html, text):
    assert plain_paragraphs(html) == text


def test_excerpt_keeps_the_paragraphs_that_fit():
    assert excerpt("First paragraph.\n\nSecond   one.", 800) == "First paragraph.\n\nSecond one."


def test_excerpt_cuts_after_the_last_whole_paragraph():
    text = "A first paragraph that ends here\n\nand a second that runs past the limit"
    assert excerpt(text, 50) == "A first paragraph that ends here"


def test_excerpt_cuts_after_the_last_whole_sentence():
    text = "The first sentence ends. The second “ends too.” The third does not end before the limit"
    assert excerpt(text, 60) == "The first sentence ends. The second “ends too.”"


def test_excerpt_cuts_at_a_word_when_no_sentence_ends_late_enough():
    text = "Short. " + "word " * 40
    assert excerpt(text, 60) == "Short. " + " ".join(["word"] * 10) + "…"


def test_holds_all_allows_for_captions_and_bylines():
    assert holds_all("word " * 90, 100)
    assert not holds_all("word " * 89, 100)


def test_paragraphs_escape_each_paragraph():
    assert paragraphs("One & two\n\nthree", "<b>", "</b>") == "<p><b>One &amp; two</p>\n<p>three</b></p>"
