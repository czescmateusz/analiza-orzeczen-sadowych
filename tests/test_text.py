from orzeczenia.text import html_to_text, split_sections

SAMPLE_HTML = """<p>
<strong>
<!-- -->
        <em>
<!-- -->Sygn. akt II Ca 69/13</em>
      </strong>
</p>
    <div>
      <h2>WYROK</h2>
      <p>sprawy z powództwa <strong>
<span class="anon-block">A. N.</span>
</strong>
</p>
      <p>I.\xa0
        zmienia zaskarżony wyrok w ten sposób, że zasądzoną od strony pozwanej na rzecz
        powoda <span class="anon-block">A. N.</span>kwotę 3.000 zł podwyższa do 7.000 zł</p>
    </div>
    <div>
      <h2>UZASADNIENIE</h2>
      <p>Sąd Rejonowy w Kłodzku wyrokiem z dnia 28 listopada 2012 roku zasądził</p>
    </div>"""


def test_html_to_text_one_line_per_block():
    text = html_to_text(SAMPLE_HTML)
    assert text.splitlines()[:3] == ["Sygn. akt II Ca 69/13", "WYROK", "sprawy z powództwa A. N."]
    assert "powoda A. N. kwotę 3.000 zł podwyższa do 7.000 zł" in text
    assert "<!--" not in text and "\xa0" not in text


def test_split_sections():
    operative, reasoning = split_sections(html_to_text(SAMPLE_HTML))
    assert operative.endswith("podwyższa do 7.000 zł")
    assert reasoning.startswith("Sąd Rejonowy w Kłodzku")


def test_split_sections_spaced_heading():
    operative, reasoning = split_sections("WYROK\nzasądza 10 zł\nU z a s a d n i e n i e\nPowód wniósł")
    assert operative == "WYROK\nzasądza 10 zł"
    assert reasoning == "Powód wniósł"


def test_split_sections_without_reasoning():
    assert split_sections("WYROK\nzasądza 10 zł") == ("WYROK\nzasądza 10 zł", "")


def test_heading_word_inside_sentence_is_not_a_split():
    text = "WYROK\nw uzasadnieniu sąd wskazał\nUzasadnienie\nPowód"
    assert split_sections(text) == ("WYROK\nw uzasadnieniu sąd wskazał", "Powód")
