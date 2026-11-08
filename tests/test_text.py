from __future__ import annotations

import json

import pytest

from vidpipe import cli, text
from vidpipe.errors import BadInput


def test_splits_on_terminators_and_keeps_punctuation():
    assert text.split_sentences("台北的夜晚，從一碗熱湯開始。雨剛停！你聽見了嗎？") == [
        "台北的夜晚，從一碗熱湯開始。", "雨剛停！", "你聽見了嗎？"]
    assert text.split_sentences("甲；乙") == ["甲；", "乙"]


def test_closing_quotes_and_repeated_marks_stay_with_their_sentence():
    assert text.split_sentences("他說：「走吧。」然後就走了。") == [
        "他說：「走吧。」", "然後就走了。"]
    assert text.split_sentences("好。。。 嗯…… 然後呢？？！") == [
        "好。。。", "嗯……", "然後呢？？！"]
    assert text.split_sentences("她笑了（真的。）接著說") == ["她笑了（真的。）", "接著說"]


def test_newlines_always_split_even_without_punctuation():
    assert text.split_sentences("第一行\n\n第二行沒有標點\r\n第三行。") == [
        "第一行", "第二行沒有標點", "第三行。"]


def test_english_periods_split_only_at_real_sentence_ends():
    out = text.split_sentences("價格是 3.5 元。Visit example.com now. Mr. Lin said v1.2 is OK!")
    assert out == ["價格是 3.5 元。", "Visit example.com now.", "Mr. Lin said v1.2 is OK!"]


def test_punctuation_only_fragments_merge_into_previous_sentence():
    assert text.split_sentences("好。\n。。。\n再見。") == ["好。。。。", "再見。"]
    assert text.split_sentences("。。。") == []
    assert text.split_sentences("   \n\n  ") == []
    assert text.split_sentences("") == []


def test_long_sentences_split_at_commas_without_exceeding_the_limit():
    clause = "這一小段大約十五個字左右，"  # 13 字
    sentence = clause * 8 + "最後一段。"
    parts = text.split_sentences(sentence, max_chars=40)
    assert len(parts) > 1 and "".join(parts) == sentence
    assert all(len(p) <= 40 for p in parts)
    assert all(p.endswith(("，", "。")) for p in parts)  # 只在逗號處切，不砍字


def test_long_sentence_without_commas_is_left_whole():
    sentence = "沒有逗號" * 20 + "。"
    assert text.split_sentences(sentence, max_chars=20) == [sentence]


def test_short_sentences_are_never_merged_or_split():
    assert text.split_sentences("夠了。走。") == ["夠了。", "走。"]


def test_has_speech():
    assert text.has_speech("好") and text.has_speech("A1") and text.has_speech("「好」")
    assert not text.has_speech("。。。 …… ") and not text.has_speech("")


def test_max_chars_validation():
    with pytest.raises(BadInput, match="max_chars"):
        text.split_sentences("好。", max_chars=3)


def test_cli_split_from_text_file_and_stdin(tmp_path, capsys, monkeypatch):
    script = tmp_path / "script.txt"
    script.write_text("第一句。第二句！\n第三句", encoding="utf-8")
    assert cli.main(["split", str(script)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["count"] == 3
    assert data["sentences"][1] == {"index": 1, "text": "第二句！", "chars": 4}
    assert cli.main(["split", "--text", "只有一句。"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1

    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("來自 stdin。"))
    assert cli.main(["split", "-"]) == 0
    assert json.loads(capsys.readouterr().out)["sentences"][0]["text"] == "來自 stdin。"

    assert cli.main(["split"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "bad_input"
    assert cli.main(["split", str(tmp_path / "missing.txt")]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "not_found"
