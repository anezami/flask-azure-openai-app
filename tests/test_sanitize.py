import pytest
from app import sanitize_model_output

@pytest.mark.parametrize(
    'raw,expected_contains', [
        ("Translation:\nHello world", "Hello world"),
        ("Here is the translation:\nBonjour le monde", "Bonjour le monde"),
        ("```\nCorrected text:\nThis is fine.\n```", "This is fine."),
        ("```text\nHere is the corrected text: Dies ist gut.\n```", "Dies ist gut."),
        ("Corrected text:\nDies ist ein Test.", "Dies ist ein Test."),
        ("Here is the correction: Linha", "Linha"),
    ]
)
def test_sanitize_basic(raw, expected_contains):
    cleaned = sanitize_model_output(raw)
    assert expected_contains in cleaned
    # Ensure we removed labels like 'Translation:' or 'Corrected text:' at start
    assert not cleaned.lower().startswith(('translation:', 'corrected text:', 'here is'))


def test_sanitize_no_change_on_plain():
    text = "Plain output with no boilerplate."
    assert sanitize_model_output(text) == text


def test_empty_input():
    assert sanitize_model_output("") == ""
