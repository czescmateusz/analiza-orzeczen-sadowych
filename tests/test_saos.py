from types import SimpleNamespace

import pytest
import requests

from orzeczenia.saos import SaosClient, SaosError

MAINTENANCE = "<!DOCTYPE html><html><head><title>Przerwa techniczna</title></head></html>"


def response(status, text="", json_data=None):
    def _json():
        if json_data is None:
            raise requests.JSONDecodeError("Expecting value", text, 0)
        return json_data

    return SimpleNamespace(status_code=status, text=text, url="u", json=_json)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        return self.responses.pop(0)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("orzeczenia.saos.time.sleep", lambda s: None)
    c = SaosClient(delay=0, max_retries=3)
    c.max_maintenance_waits = 10
    return c


def test_waits_out_maintenance_without_using_retries(client):
    client.session = FakeSession([response(200, MAINTENANCE)] * 5 + [response(200, json_data={"data": {"id": 1}})])
    assert client.judgment(1) == {"id": 1}


def test_gives_up_after_too_much_maintenance(client):
    client.session = FakeSession([response(200, MAINTENANCE)] * 11)
    with pytest.raises(SaosError, match="maintenance"):
        client.judgment(1)


def test_retries_non_json_200(client):
    client.session = FakeSession([response(200, "<html>oops"), response(200, json_data={"data": {"id": 2}})])
    assert client.judgment(2) == {"id": 2}


def test_404_returns_none_and_400_raises(client):
    client.session = FakeSession([response(404)])
    assert client.judgment(3) is None
    client.session = FakeSession([response(400, "bad")])
    with pytest.raises(SaosError, match="400"):
        client.judgment(4)
