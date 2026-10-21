from app.config import ServerCfg
from app.netdata import parse_json2_by_node
from app.plex import normalize_session

SRV = ServerCfg(id=1, name="Atlas", plex_url="http://10.0.0.11:32400", token="x", netdata_node="node-atlas")


def episode_session(**ts):
    item = {
        "type": "episode", "title": "Departure", "grandparentTitle": "Snowfall", "parentIndex": 6, "index": 4,
        "ratingKey": "123", "sessionKey": "42", "duration": 2_770_000, "viewOffset": 744_000,
        "grandparentThumb": "/library/metadata/100/thumb/1",
        "User": {"title": "sable"},
        "Player": {"title": "LG TV", "platform": "webOS", "product": "Plex for LG", "version": "1.9", "state": "playing", "local": True},
        "Session": {"id": "abc", "bandwidth": 24000, "location": "lan"},
        "Media": [{"videoResolution": "4k", "videoCodec": "hevc", "audioCodec": "truehd", "audioChannels": 8, "container": "mkv",
                   "Part": [{"Stream": [{"streamType": 1, "codec": "hevc", "colorTrc": "smpte2084"},
                                        {"streamType": 2, "codec": "truehd", "channels": 8, "selected": True}]}]}],
    }
    if ts:
        item["TranscodeSession"] = ts
    return item


def test_direct_play():
    row = normalize_session(SRV, episode_session())
    assert row["title"] == "Snowfall"
    assert row["kind"] == "Episode"
    assert row["subtitle"].startswith("S6")
    assert row["method"] == "Direct play"
    assert row["resolution"] == "4K"
    assert row["source_video"] == "HEVC 4K HDR10"
    assert row["bitrate_mbps"] == 24.0
    assert row["progress_pct"] == 27
    assert row["location"] == "LAN"
    assert row["state"] == "Playing"
    assert row["id"] == "Atlas-42"


def test_transcode():
    row = normalize_session(SRV, episode_session(videoDecision="transcode", audioDecision="transcode", container="mpegts",
                                                 videoCodec="h264", audioCodec="aac", audioChannels=2, height=720, speed="0.9", throttled=False))
    assert row["method"] == "Transcode"
    assert row["stream_video"] == "H264 720p"
    assert row["transcode_reason"] == "Quality or bandwidth limit"
    assert row["transcode_speed"] == 0.9
    assert row["hw_accel"] == "None (CPU)"
    assert row["container"] == "MKV → MPEGTS"


def test_netdata_parse_by_id_and_name():
    resp = {
        "nodes": [{"nd": "node-atlas", "nm": "atlas-plex"}, {"nd": "node-orion", "nm": "orion"}],
        "result": {"labels": ["time", "atlas-plex", "orion"], "data": [[1700000000, [34.2, 0, 0], [91.0, 0, 0]]]},
    }
    servers = [SRV, ServerCfg(id=2, name="Orion", plex_url="http://x:32400", netdata_node="orion")]
    assert parse_json2_by_node(resp, servers) == {"Atlas": 34.2, "Orion": 91.0}


def test_bogus_10000_mbps_counts_as_15():
    item = episode_session(videoDecision="transcode", audioDecision="copy", container="mp4", videoCodec="h264", height=1080, speed="0.0", throttled=True)
    item["Session"]["bandwidth"] = 10_000_000  # Plex bug: 10,000 Mbps
    row = normalize_session(SRV, item)
    assert row["bitrate_mbps"] == 15.0
    assert row["bitrate_estimated"] is True and row["bitrate_reported_mbps"] == 10000.0
    assert row["quality"].startswith("15 Mbps")


def test_real_bandwidth_untouched():
    row = normalize_session(SRV, episode_session())
    assert row["bitrate_mbps"] == 24.0 and row["bitrate_estimated"] is False
