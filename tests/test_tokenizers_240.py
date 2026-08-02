from __future__ import annotations

from unittest.mock import patch

from simplicio_fast.tokenizers import resolve_tokenizer


def test_unconfigured_or_unknown_tokenizer_is_explicitly_unavailable() -> None:
    assert resolve_tokenizer(None) is None
    assert resolve_tokenizer("whitespace-v1") is None


def test_tiktoken_encoding_is_resolved_without_making_dependency_required() -> None:
    fake_encoding = type("Encoding", (), {"encode": lambda self, text: [*text]})()
    fake_module = type(
        "Tiktoken",
        (),
        {"get_encoding": staticmethod(lambda name: fake_encoding)},
    )
    with patch.dict("sys.modules", {"tiktoken": fake_module}):
        tokenizer = resolve_tokenizer("tiktoken:cl100k_base")
    assert tokenizer is not None
    assert tokenizer("abc") == 3


def test_unknown_tiktoken_encoding_falls_back_without_claiming_exactness() -> None:
    fake_module = type(
        "Tiktoken",
        (),
        {"get_encoding": staticmethod(lambda name: (_ for _ in ()).throw(KeyError(name)))},
    )
    with patch.dict("sys.modules", {"tiktoken": fake_module}):
        assert resolve_tokenizer("tiktoken:missing") is None
