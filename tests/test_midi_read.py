"""Reading a .mid into note-bridge notes (fl_read_midi_file)."""
from __future__ import annotations

import json

import mido
import pytest
from fastmcp import Client

from fl_studio_mcp.music.midi_export import read_midi, write_midi
from fl_studio_mcp.server import build_server
from fl_studio_mcp.tools.pianoroll import PianoRollNote


def _save(path, tracks, ppq=96):
    mf = mido.MidiFile(type=1, ticks_per_beat=ppq)
    for messages in tracks:
        mf.tracks.append(mido.MidiTrack(messages))
    mf.save(path)
    return str(path)


def _on(note, time=0, velocity=100, channel=0):
    return mido.Message("note_on", note=note, velocity=velocity, time=time, channel=channel)


def _off(note, time=0, channel=0):
    return mido.Message("note_off", note=note, velocity=0, time=time, channel=channel)


def _notes(result):
    return [n for t in result["tracks"] for n in t["notes"]]


def test_round_trip_through_writer(tmp_path):
    spec = [{"name": "Lead", "channel": 2, "notes": [
        {"pitch": 60, "start_bars": 0.0, "length_bars": 0.25, "velocity": 100 / 127},
        {"pitch": 64, "start_bars": 0.5, "length_bars": 0.125, "velocity": 1 / 127},
        {"pitch": 67, "start_bars": 1.75, "length_bars": 2.0, "velocity": 1.0},
    ]}, {"name": "Bass", "channel": 0, "notes": [
        {"pitch": 36, "start_bars": 1.0, "length_bars": 1.0, "velocity": 0.5},
    ]}]
    path = str(tmp_path / "song.mid")
    write_midi(spec, 120, path)

    result = read_midi(path)
    assert [(t["track"], t["name"], t["channel"]) for t in result["tracks"]] == [
        (1, "Lead", 2), (2, "Bass", 0)]

    # read -> writer -> read must not move anything
    again = [{"name": t["name"], "channel": t["channel"], "notes": [
        {"pitch": n["pitch"], "start_bars": n["time_bars"], "length_bars": n["length_bars"],
         "velocity": n["velocity"]} for n in t["notes"]]} for t in result["tracks"]]
    path2 = str(tmp_path / "again.mid")
    write_midi(again, 120, path2)
    assert read_midi(path2)["tracks"] == result["tracks"]

    lead = result["tracks"][0]["notes"]
    assert [(n["pitch"], n["time_bars"], n["length_bars"]) for n in lead] == [
        (60, 0.0, 0.25), (64, 0.5, 0.125), (67, 1.75, 2.0)]
    assert lead[0]["velocity"] == pytest.approx(100 / 127, abs=1e-4)
    assert round(lead[1]["velocity"] * 127) == 1 and lead[2]["velocity"] == 1.0


def test_notes_are_accepted_by_the_piano_roll_writer(tmp_path):
    path = _save(tmp_path / "a.mid", [[_on(60), _off(60, 0), _on(62, 96, velocity=1), _off(62, 1)]])
    for note in _notes(read_midi(path)):
        PianoRollNote(**note)        # a zero-length input must still validate


@pytest.mark.parametrize("ppq", [96, 480, 960])
def test_position_uses_ticks_per_beat(tmp_path, ppq):
    path = _save(tmp_path / "a.mid", [[_on(60, ppq), _off(60, ppq * 2)]], ppq=ppq)
    result = read_midi(path)
    assert result["ticks_per_beat"] == ppq
    assert _notes(result) == [{"pitch": 60, "time_bars": 0.25, "length_bars": 0.5,
                               "velocity": round(100 / 127, 4)}]


def test_note_on_velocity_zero_ends_a_note(tmp_path):
    path = _save(tmp_path / "a.mid", [[_on(60), _on(60, 96, velocity=0)]])
    result = read_midi(path)
    assert [n["length_bars"] for n in _notes(result)] == [0.25]
    assert result["unterminated_notes"] == 0


def test_overlapping_same_pitch_closes_oldest_first(tmp_path):
    # on@0, on@96, off@192, off@288: two notes, neither is dropped
    path = _save(tmp_path / "a.mid", [[_on(60, velocity=50), _on(60, 96, velocity=90),
                                       _off(60, 96), _off(60, 96)]])
    assert [(n["time_bars"], n["length_bars"], round(n["velocity"] * 127))
            for n in _notes(read_midi(path))] == [(0.0, 0.5, 50), (0.25, 0.5, 90)]


def test_same_pitch_on_other_channel_is_separate(tmp_path):
    path = _save(tmp_path / "a.mid", [[_on(60, channel=0), _on(60, 96, channel=1),
                                       _off(60, 96, channel=0), _off(60, 96, channel=1)]])
    assert [(t["channel"], t["notes"][0]["length_bars"]) for t in read_midi(path)["tracks"]] == [
        (0, 0.5), (1, 0.5)]


def test_stray_note_off_is_ignored_and_unclosed_note_ends_with_track(tmp_path):
    path = _save(tmp_path / "a.mid", [[_off(70), _on(60), mido.MetaMessage("end_of_track", time=192)]])
    result = read_midi(path)
    assert [(n["pitch"], n["length_bars"]) for n in _notes(result)] == [(60, 0.5)]
    assert result["unterminated_notes"] == 1


def test_three_four_keeps_ticks_for_the_note_bridge(tmp_path):
    spec = [{"name": "W", "notes": [{"pitch": 60, "start_bars": 1.0, "length_bars": 1.0}]}]
    path = str(tmp_path / "waltz.mid")
    write_midi(spec, 120, path, ppq=96, beats_per_bar=3)
    result = read_midi(path)
    assert result["time_signatures"] == [{"time_bars": 0.0, "numerator": 3, "denominator": 4}]
    note = _notes(result)[0]
    # the bridge writes time_bars * PPQ * 4: bar 2 of 3/4 is 3 quarter notes in
    assert note["time_bars"] * 96 * 4 == 3 * 96
    assert note["length_bars"] * 96 * 4 == 3 * 96


def test_time_signature_change_on_a_later_track_and_default(tmp_path):
    sig = mido.MetaMessage("time_signature", numerator=6, denominator=8, time=384)
    path = _save(tmp_path / "a.mid", [[_on(60), _off(60, 96)], [sig]])
    assert read_midi(path)["time_signatures"] == [{"time_bars": 1.0, "numerator": 6, "denominator": 8}]
    path = _save(tmp_path / "b.mid", [[_on(60), _off(60, 96)]])
    result = read_midi(path)
    assert result["time_signatures"][0]["numerator"] == 4 and result["time_signature_count"] == 0


def test_time_signatures_are_bounded(tmp_path):
    sigs = [mido.MetaMessage("time_signature", numerator=4, denominator=4, time=96) for _ in range(100)]
    result = read_midi(_save(tmp_path / "a.mid", [sigs]))
    assert len(result["time_signatures"]) == 32 and result["time_signature_count"] == 100


def test_pages_cover_every_note_once_across_tracks(tmp_path):
    def run(pitch):
        return [m for i in range(5) for m in (_on(pitch + i), _off(pitch + i, 10))]
    path = _save(tmp_path / "a.mid", [run(40), [], run(60)])

    seen, start, sizes = [], 0, []
    while start is not None:
        result = read_midi(path, start, page=3)
        assert result["total_notes"] == 10
        sizes.append(len(_notes(result)))
        seen += [n["pitch"] for n in _notes(result)]
        start = result["next_start"]
    assert sizes == [3, 3, 3, 1]
    assert seen == list(range(40, 45)) + list(range(60, 65))


def test_start_past_the_end_is_empty(tmp_path):
    result = read_midi(_save(tmp_path / "a.mid", [[_on(60), _off(60, 96)]]), start=5)
    assert result["tracks"] == [] and result["next_start"] is None


def test_file_without_notes(tmp_path):
    result = read_midi(_save(tmp_path / "a.mid", [[]]))
    assert result["total_notes"] == 0 and result["tracks"] == []


_HEADER = bytes.fromhex("4d546864 00000006 0001 0001")
_EMPTY_TRACK = bytes.fromhex("4d54726b 00000004 00ff2f00")


async def _tool(**args):
    async with Client(build_server()) as client:
        result = await client.call_tool("fl_read_midi_file", args)
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_tool_reads_a_file(tmp_path):
    path = _save(tmp_path / "a.mid", [[_on(60), _off(60, 96)]])
    result = await _tool(path=path)
    assert result["ok"] is True and result["path"] == path
    assert _notes(result)[0]["pitch"] == 60


@pytest.mark.asyncio
async def test_tool_reports_missing_path(tmp_path):
    result = await _tool(path=str(tmp_path / "nope.mid"))
    assert result["ok"] is False and "file not found" in result["error"]
    result = await _tool(path=str(tmp_path))       # a folder is not a file
    assert result["ok"] is False and "file not found" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    b"",
    b"hello, not midi",
    _HEADER + bytes.fromhex("01e0 4d54726b 00000040 00903c"),   # cut short
    _HEADER + bytes.fromhex("e728") + _EMPTY_TRACK,             # SMPTE division
    _HEADER + bytes.fromhex("0000") + _EMPTY_TRACK,             # zero division
])
async def test_tool_reports_unreadable_file(tmp_path, data):
    path = tmp_path / "bad.mid"
    path.write_bytes(data)
    result = await _tool(path=str(path))
    assert result["ok"] is False and "not a readable MIDI file" in result["error"]
