"""Functional tests for RobotImageResource (/robot-images)."""
import io
import json
from unittest.mock import MagicMock


def multipart(metadata_map=None, **form):
    """Builds a multipart form body containing one dummy image file."""
    data = {"file": (io.BytesIO(b"fake-image-bytes"), "photo.png"), **form}
    if metadata_map is not None:
        data["metadata_map"] = json.dumps(metadata_map)
    return data


def test_get_requires_api_key(client):
    assert client.get("/robot-images").status_code == 401


def test_get_filters_by_scan_id(client, auth, mock_db):
    mock_db.description = [("image_id",), ("scan_id",)]
    mock_db.fetchall.return_value = [("img-1", "scan-1")]

    res = client.get("/robot-images?scan_id=scan-1", headers=auth)

    assert res.status_code == 200
    assert res.json == [{"image_id": "img-1", "scan_id": "scan-1"}]
    sql, params = mock_db.execute.call_args.args
    assert "scan_id = %s" in sql and "scan-1" in params


def test_post_uploads_image(client, auth, mock_db, monkeypatch):
    minio = MagicMock()
    monkeypatch.setattr("resources.robot.minio_client", minio)
    monkeypatch.setattr("resources.robot.send_avro_message", MagicMock(return_value=True))
    monkeypatch.setattr("resources.robot.send_simple_message", MagicMock())

    form = multipart({"photo.png": {"robot_pose": "x=1,y=2"}}, scan_id="scan-1")
    res = client.post("/robot-images", data=form, headers=auth)

    assert res.status_code == 201
    assert res.json["scan_id"] == "scan-1"
    [uploaded] = res.json["uploaded_files"]
    assert uploaded["filename"] == "photo.png"
    assert uploaded["robot_pose"] == "x=1,y=2"
    minio.put_object.assert_called_once()
    mock_db.execute.assert_called_once()  # the metadata row was inserted


def test_post_rejects_missing_file_or_metadata(client, auth):
    no_file = client.post("/robot-images", data={"metadata_map": "{}"}, headers=auth)
    no_map = client.post("/robot-images", data={"file": (io.BytesIO(b"x"), "a.png")}, headers=auth)

    assert no_file.status_code == 400
    assert no_map.status_code == 400


def test_post_rejects_invalid_metadata_json(client, auth):
    res = client.post("/robot-images", data=multipart() | {"metadata_map": "{not json"}, headers=auth)
    assert res.status_code == 400


def test_post_returns_500_when_kafka_fails(client, auth, monkeypatch):
    monkeypatch.setattr("resources.robot.minio_client", MagicMock())
    monkeypatch.setattr("resources.robot.send_avro_message", MagicMock(return_value=False))

    res = client.post("/robot-images", data=multipart({}), headers=auth)

    assert res.status_code == 500
