import json

import httpx
import pytest

from app.ai.decision import AgentDecision
from app.ai.local import ModelUnavailable, OllamaProvider, OpenAICompatibleProvider, decoding_schema


@pytest.mark.parametrize('cls', [OllamaProvider, OpenAICompatibleProvider])
async def test_structured_transport_schema_and_decision(cls):
    async def handle(request):
        body = json.loads(request.content)
        assert body['model'] == 'replaceable-model'
        if cls is OllamaProvider:
            assert body['format']['properties']['action']
            return httpx.Response(200, json={'message': {'content': '{"action":"SHOW_RESULTS"}'}})
        assert body['response_format']['type'] == 'json_schema'
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"action":"SHOW_RESULTS"}'}}]})
    provider = cls('http://localhost:11434', 'replaceable-model')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        raw = await provider.request(client, [], AgentDecision.model_json_schema())
    assert AgentDecision.model_validate_json(raw).action == 'SHOW_RESULTS'


async def test_invalid_json_repair_bounded():
    class Invalid(OllamaProvider):
        calls = 0
        async def request(self, *args):
            self.calls += 1
            return 'not JSON'
    provider = Invalid('http://localhost:11434', 'model', retries=2)
    with pytest.raises(ModelUnavailable):
        await provider.decide({}, 'hello')
    assert provider.calls == 3


def test_decoding_grammar_keeps_types_but_server_enforces_bounds():
    from pydantic import ValidationError

    schema = decoding_schema(AgentDecision.model_json_schema())
    assert 'maxItems' not in json.dumps(schema)
    assert schema['$defs']['AgentAction']['enum']
    with pytest.raises(ValidationError):
        AgentDecision.model_validate({'action': 'SELECT_PRODUCTS', 'selection': {'count': 10000}})
