import pytest

from app.trading_agent.model import DeepSeekModel, AuthenticationError, InvalidModelResponse, parse_tool_arguments


class Response:
    status_code = 200
    def json(self):
        return {"choices":[{"message":{"content":None,"tool_calls":[{"id":"call-1","function":{"name":"query_positions","arguments":'{"asset_type":"option"}'}}]},"finish_reason":"tool_calls"}],"usage":{"total_tokens":12}}


class Session:
    def __init__(self): self.payload = None
    def post(self, url, **kwargs): self.payload = kwargs; return Response()


def test_deepseek_disables_thinking_and_parses_tool_call():
    session = Session()
    turn = DeepSeekModel(api_key="synthetic", base_url="http://localhost", session=session).next_turn([], [], 15)
    assert turn.tool_calls[0].name == "query_positions"
    assert session.payload["json"]["thinking"] == {"type":"disabled"}
    assert "Authorization" in session.payload["headers"]


def test_model_auth_failure_is_not_retried():
    class AuthResponse(Response): status_code = 401
    class AuthSession(Session):
        def post(self, *args, **kwargs): return AuthResponse()
    with pytest.raises(AuthenticationError):
        DeepSeekModel(api_key="synthetic", base_url="http://localhost", session=AuthSession()).next_turn([], [], 1)


def test_duplicate_or_nonfinite_tool_json_is_rejected():
    with pytest.raises(InvalidModelResponse):
        parse_tool_arguments('{"a":1,"a":2}')
    with pytest.raises(InvalidModelResponse):
        parse_tool_arguments('{"a":NaN}')
