from types import SimpleNamespace

import pytest

from orzeczenia.extract import Extractor, parse_pln, parse_response


@pytest.mark.parametrize(
    "text, expected",
    [
        ("80.000 zł", 80000.0),
        ("80 000", 80000.0),
        ("4.172,60 zł", 4172.60),
        ("1.000.000,00 złotych", 1000000.0),
        ("1250.5", 1250.5),
        (15000, 15000),
        (None, None),
        ("brak", None),
    ],
)
def test_parse_pln(text, expected):
    assert parse_pln(text) == expected


REPLY = """```json
{"is_road_accident": true, "claimants": [{"role": "poszkodowany", "sex": "K", "age_at_accident": 34,
 "injuries": ["złamanie kości udowej"], "permanent_damage_percent": 15,
 "awards": [{"type": "zadośćuczynienie", "amount_appropriate": "80.000 zł",
             "amount_paid_earlier": 20000, "amount_awarded": 60000, "evidence": "kwota 80.000 zł"}]}]}
```"""


def test_parse_response_tolerates_fences_and_polish_amounts():
    result = parse_response(REPLY)
    award = result.claimants[0].awards[0]
    assert award.amount_appropriate == 80000.0
    assert award.amount_awarded == 60000
    assert result.claimants[0].injuries == ["złamanie kości udowej"]


def test_parse_response_rejects_unknown_award_type():
    with pytest.raises(Exception):
        parse_response('{"is_road_accident": true, "claimants": [{"role": "poszkodowany",'
                       ' "awards": [{"type": "nawiązka"}]}]}')


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])


def test_extractor_returns_parsed_result():
    client = FakeClient([REPLY])
    raw, result, error = Extractor(client=client, delay=0).extract("SENTENCJA: ...")
    assert error is None and result.is_road_accident
    assert "SENTENCJA: ..." in client.calls[0]["messages"][1]["content"]
    assert client.calls[0]["response_format"]["type"] == "json_schema"


class FakeAnthropic:
    def __init__(self, reply):
        self.calls = []
        outer = self

        class Messages:
            def parse(self, **kwargs):
                outer.calls.append(kwargs)
                return SimpleNamespace(content=[SimpleNamespace(type="text", text=reply)])

        self.messages = Messages()


def test_claude_models_use_anthropic_backend_with_schema():
    client = FakeAnthropic(REPLY.strip("`json\n"))
    extractor = Extractor(model="claude-haiku-4-5", client=client, delay=0)
    _, result, error = extractor.extract("SENTENCJA: ...")
    assert extractor.backend == "anthropic" and error is None
    assert result.claimants[0].awards[0].amount_appropriate == 80000
    call = client.calls[0]
    assert call["model"] == "claude-haiku-4-5" and call["output_format"].__name__ == "Extraction"
    assert "SENTENCJA: ..." in call["messages"][0]["content"]


def test_env_value_falls_back_to_env_file(tmp_path, monkeypatch):
    from orzeczenia.extract import _env_value

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text('OTHER=1\nANTHROPIC_API_KEY="sk-test"\n', encoding="utf-8")
    assert _env_value("ANTHROPIC_API_KEY", env) == "sk-test"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert _env_value("ANTHROPIC_API_KEY", env) == "from-env"


def test_analysis_field_comes_first_in_schema():
    from orzeczenia.extract import RESPONSE_SCHEMA

    assert list(RESPONSE_SCHEMA["schema"]["properties"])[0] == "analysis"


def test_extractor_reports_unparseable_reply():
    raw, result, error = Extractor(client=FakeClient(["Nie wiem."]), delay=0).extract("x")
    assert result is None and "no JSON object" in error and raw == "Nie wiem."


def test_extractor_retries_server_errors(monkeypatch):
    monkeypatch.setattr("orzeczenia.extract.time.sleep", lambda s: None)
    server_error = RuntimeError("503")
    server_error.response = SimpleNamespace(status_code=503)
    client = FakeClient([server_error, REPLY])
    _, result, error = Extractor(client=client, delay=0).extract("x")
    assert result is not None and len(client.calls) == 2


def test_extractor_does_not_retry_client_errors(monkeypatch):
    monkeypatch.setattr("orzeczenia.extract.time.sleep", lambda s: None)
    auth_error = RuntimeError("401")
    auth_error.response = SimpleNamespace(status_code=401)
    with pytest.raises(RuntimeError, match="401"):
        Extractor(client=FakeClient([auth_error]), delay=0).extract("x")
