import anyio

from mcp_google_workspace.meet.client import normalize_conference_record_name, normalize_space_name
from mcp_google_workspace.meet.server import meet_mcp
from mcp_google_workspace.meet.tools import (
    CreateSpaceRequest,
    EndActiveConferenceRequest,
    GetConferenceRecordRequest,
    GetSpaceRequest,
    ListConferenceParticipantsRequest,
    ListConferenceRecordingsRequest,
    ListConferenceRecordsRequest,
    ListConferenceTranscriptsRequest,
    UpdateSpaceRequest,
    create_space_payload,
    end_active_conference_payload,
    get_conference_record_payload,
    get_space_payload,
    list_conference_participants_payload,
    list_conference_recordings_payload,
    list_conference_records_payload,
    list_conference_transcripts_payload,
    update_space_payload,
)


async def _list_tool_names(server):
    tools = await server.list_tools(run_middleware=False)
    return [tool.name for tool in tools]


async def _get_tool(server, name):
    tools = await server.list_tools(run_middleware=False)
    return next(tool for tool in tools if tool.name == name)


class _Exec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _ParticipantsApi:
    def __init__(self, parent):
        self.parent = parent

    def list(self, **kwargs):
        self.parent.calls.append(("participants.list", kwargs))
        return _Exec({"kind": "conferenceRecords.participants.list", "kwargs": kwargs})


class _RecordingsApi:
    def __init__(self, parent):
        self.parent = parent

    def list(self, **kwargs):
        self.parent.calls.append(("recordings.list", kwargs))
        return _Exec({"kind": "conferenceRecords.recordings.list", "kwargs": kwargs})


class _TranscriptsApi:
    def __init__(self, parent):
        self.parent = parent

    def list(self, **kwargs):
        self.parent.calls.append(("transcripts.list", kwargs))
        return _Exec({"kind": "conferenceRecords.transcripts.list", "kwargs": kwargs})


class _ConferenceRecordsApi:
    def __init__(self):
        self.calls = []
        self.participants_api = _ParticipantsApi(self)
        self.recordings_api = _RecordingsApi(self)
        self.transcripts_api = _TranscriptsApi(self)

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return _Exec({"kind": "conferenceRecords.list", "kwargs": kwargs})

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return _Exec({"kind": "conferenceRecords.get", "kwargs": kwargs})

    def participants(self):
        return self.participants_api

    def recordings(self):
        return self.recordings_api

    def transcripts(self):
        return self.transcripts_api


class _SpacesApi:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return _Exec({"kind": "spaces.create", "kwargs": kwargs})

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return _Exec({"kind": "spaces.get", "kwargs": kwargs})

    def patch(self, **kwargs):
        self.calls.append(("patch", kwargs))
        return _Exec({"kind": "spaces.patch", "kwargs": kwargs})

    def endActiveConference(self, **kwargs):
        self.calls.append(("endActiveConference", kwargs))
        return _Exec({"kind": "spaces.endActiveConference", "kwargs": kwargs})


class _MeetService:
    def __init__(self):
        self.spaces_api = _SpacesApi()
        self.records_api = _ConferenceRecordsApi()

    def spaces(self):
        return self.spaces_api

    def conferenceRecords(self):
        return self.records_api


def test_meet_server_registers_expected_tools():
    tool_names = anyio.run(_list_tool_names, meet_mcp)
    assert "create_space" in tool_names
    assert "get_space" in tool_names
    assert "update_space" in tool_names
    assert "end_active_conference" in tool_names
    assert "list_conference_records" in tool_names
    assert "get_conference_record" in tool_names
    assert "list_conference_participants" in tool_names
    assert "list_conference_recordings" in tool_names
    assert "list_conference_transcripts" in tool_names


def test_meet_name_normalization():
    assert normalize_space_name("abc") == "spaces/abc"
    assert normalize_space_name("spaces/abc") == "spaces/abc"
    assert normalize_conference_record_name("def") == "conferenceRecords/def"
    assert normalize_conference_record_name("conferenceRecords/def") == "conferenceRecords/def"


def test_meet_payload_helpers(monkeypatch):
    service = _MeetService()
    monkeypatch.setattr("mcp_google_workspace.meet.tools.meet_service", lambda: service)

    created = create_space_payload(CreateSpaceRequest(config={"accessType": "OPEN"}))
    space = get_space_payload(GetSpaceRequest(space_name="abc"))
    updated = update_space_payload(UpdateSpaceRequest(space_name="abc", config={"entryPointAccess": "ALL"}))
    ended = end_active_conference_payload(EndActiveConferenceRequest(space_name="abc"))
    records = list_conference_records_payload(ListConferenceRecordsRequest(page_size=10))
    record = get_conference_record_payload(GetConferenceRecordRequest(conference_record_name="rec-1"))
    participants = list_conference_participants_payload(
        ListConferenceParticipantsRequest(conference_record_name="rec-1", page_size=5)
    )
    recordings = list_conference_recordings_payload(
        ListConferenceRecordingsRequest(conference_record_name="rec-1", page_size=5)
    )
    transcripts = list_conference_transcripts_payload(
        ListConferenceTranscriptsRequest(conference_record_name="rec-1", page_size=5)
    )

    assert created["kwargs"]["body"]["config"]["accessType"] == "OPEN"
    assert space["kwargs"]["name"] == "spaces/abc"
    assert updated["kwargs"]["updateMask"] == "config"
    assert ended["kwargs"]["name"] == "spaces/abc"
    assert records["kwargs"]["pageSize"] == 10
    assert record["kwargs"]["name"] == "conferenceRecords/rec-1"
    assert participants["kwargs"]["parent"] == "conferenceRecords/rec-1"
    assert recordings["kwargs"]["parent"] == "conferenceRecords/rec-1"
    assert transcripts["kwargs"]["parent"] == "conferenceRecords/rec-1"


def test_meet_tool_annotations():
    get_tool = anyio.run(_get_tool, meet_mcp, "get_space")
    end_tool = anyio.run(_get_tool, meet_mcp, "end_active_conference")

    assert get_tool.annotations.readOnlyHint is True
    assert end_tool.annotations.destructiveHint is True
