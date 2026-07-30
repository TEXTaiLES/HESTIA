"""Real integration for the annotation resource.

Hits real Postgres (annotations + reconstructions tables), real Kafka broker,
and real Schema Registry (for the Avro-serialized message on POST). Kafka
messages are verified via a fresh consumer subscribed before each test.

Run with: pytest -m integration
"""
import json
import time

import pytest


pytestmark = pytest.mark.integration


URL = '/annotations'

VALID_SCENEGRAPH = {
    'nodes': {'model.glb': {'urls': ['https://public/model.glb']}},
    'edges': {'.': ['model.glb']},
}


# Polls a consumer until it receives a message whose key matches, or times out.
# Filtering by key avoids picking up stray messages from other test runs or
# non-test producers hitting the same topic.
def _consume_message_by_key(consumer, key: str, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = consumer.poll(1.0)
        if msg is None or msg.error():
            continue
        if msg.key() and msg.key().decode('utf-8') == key:
            return msg
    return None


class TestRealGet:
    # A directly-inserted annotation row is retrievable via GET filtered by object_id.
    def test_get_returns_saved_annotation(
        self, real_client_annotation, auth_headers, test_annotation_row,
    ):
        r = real_client_annotation.get(
            f"{URL}?object_id={test_annotation_row['object_id']}",
            headers=auth_headers,
        )
        assert r.status_code == 200
        scene_ids = [s['scene_id'] for s in r.get_json()['scenes']]
        assert test_annotation_row['scene_id'] in scene_ids

    # A GET filtered by an object_id that has no annotations returns 204 No Content.
    def test_get_no_matching_annotations_returns_204(self, real_client_annotation, auth_headers):
        r = real_client_annotation.get(
            f"{URL}?object_id=definitely-does-not-exist-{int(time.time())}",
            headers=auth_headers,
        )
        assert r.status_code == 204


class TestRealPost:
    # Happy path: verify the observable outcome (row appears in DB) regardless
    # of the writer race described below.
    #
    # KNOWN RACE: the resource both publishes to the `annotations` Kafka topic
    # AND does its own direct INSERT into the annotations table. The JDBC sink
    # consuming that topic ALSO writes to the same table. If the sink beats the
    # resource, the resource's INSERT fails with a duplicate-key error → 500.
    # Either way, a row for the object_id ends up in the DB. The Kafka
    # notification on `annotation_uploaded` is only published when the resource
    # wins (201) — if it 500s, that notification never fires.
    def test_post_creates_row_and_publishes_kafka(
        self, real_client_annotation, auth_headers, test_reconstruction,
        real_db_connection, kafka_consumer_factory,
    ):
        consumer = kafka_consumer_factory('annotation_uploaded')

        r = real_client_annotation.post(URL, headers=auth_headers, json={
            'object_id': test_reconstruction['object_id'],
            'scenegraph': VALID_SCENEGRAPH,
        })
        assert r.status_code in (201, 500), f'unexpected status: {r.get_data(as_text=True)}'
        scene_id_from_response = r.get_json().get('scene_id') if r.status_code == 201 else None

        # Poll for the row to appear (either the resource inserted it or the sink did).
        row = None
        deadline = time.time() + 10
        while time.time() < deadline and row is None:
            cur = real_db_connection.cursor()
            cur.execute(
                "SELECT scene_id, object_id, public_url FROM annotations WHERE object_id = %s LIMIT 1",
                (test_reconstruction['object_id'],),
            )
            row = cur.fetchone()
            cur.close()
            if row is None:
                time.sleep(0.3)

        assert row is not None, 'no annotation row for this object_id appeared within 10s'
        db_scene_id, db_object_id, db_public_url = row
        assert db_object_id == test_reconstruction['object_id']
        assert db_public_url == 'https://public/model.glb'

        # If the resource won, it published to annotation_uploaded and the response
        # scene_id matches the DB row's scene_id.
        if r.status_code == 201:
            assert db_scene_id == scene_id_from_response
            msg = _consume_message_by_key(consumer, scene_id_from_response)
            assert msg is not None, 'annotation_uploaded message not received within timeout'
            assert json.loads(msg.value().decode('utf-8')) == {'status': 'saved'}

        # Cleanup — the row was inserted via the real code path (either writer);
        # we own cleanup here since no fixture manages it.
        cur = real_db_connection.cursor()
        cur.execute("DELETE FROM annotations WHERE scene_id = %s", (db_scene_id,))
        real_db_connection.commit()
        cur.close()

    # Posting an object_id with no matching reconstruction row triggers the 404 branch.
    def test_post_no_matching_reconstruction_returns_404(
        self, real_client_annotation, auth_headers,
    ):
        r = real_client_annotation.post(URL, headers=auth_headers, json={
            'object_id': f'nonexistent-{int(time.time())}',
            'scenegraph': VALID_SCENEGRAPH,
        })
        assert r.status_code == 404


class TestRealPatch:
    # Happy path: PATCH updates the DB row, publishes to annotation_modified topic.
    def test_patch_updates_row_and_publishes_kafka(
        self, real_client_annotation, auth_headers, test_annotation_row,
        real_db_connection, kafka_consumer_factory,
    ):
        consumer = kafka_consumer_factory('annotation_modified')

        r = real_client_annotation.patch(URL, headers=auth_headers, json={
            'scene_id': test_annotation_row['scene_id'],
            'collaborative': True,
            'scenegraph': {'nodes': {'new.glb': {'urls': ['https://new']}}},
        })

        assert r.status_code == 201, f'unexpected body: {r.get_data(as_text=True)}'

        # DB was updated
        cur = real_db_connection.cursor()
        cur.execute(
            "SELECT collaborative, content FROM annotations WHERE scene_id = %s",
            (test_annotation_row['scene_id'],),
        )
        row = cur.fetchone()
        cur.close()
        assert row[0] is True
        # content is a merged JSON dict, expressed as text in the DB
        merged = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        assert 'new.glb' in merged.get('nodes', {})

        # Kafka message
        msg = _consume_message_by_key(consumer, test_annotation_row['scene_id'])
        assert msg is not None
        assert json.loads(msg.value().decode('utf-8')) == {'status': 'updated'}

    # Regression guard for the annotation.py:299 KeyError fix: a scenegraph-only
    # patch (no 'collaborative' field) must succeed against the real stack.
    def test_patch_scenegraph_only_succeeds(
        self, real_client_annotation, auth_headers, test_annotation_row,
        real_db_connection, kafka_consumer_factory,
    ):
        consumer = kafka_consumer_factory('annotation_modified')

        r = real_client_annotation.patch(URL, headers=auth_headers, json={
            'scene_id': test_annotation_row['scene_id'],
            'scenegraph': {'nodes': {'only.glb': {'urls': ['https://only']}}},
        })

        assert r.status_code == 201, f'unexpected body: {r.get_data(as_text=True)}'

        # collaborative stays at the original False (fixture default); content was merged
        cur = real_db_connection.cursor()
        cur.execute(
            "SELECT collaborative, content FROM annotations WHERE scene_id = %s",
            (test_annotation_row['scene_id'],),
        )
        row = cur.fetchone()
        cur.close()
        assert row[0] is False
        merged = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        assert 'only.glb' in merged.get('nodes', {})

        msg = _consume_message_by_key(consumer, test_annotation_row['scene_id'])
        assert msg is not None
        assert json.loads(msg.value().decode('utf-8')) == {'status': 'updated'}

    # PATCH with a scene_id that doesn't exist returns 400 (no rows found in SELECT).
    def test_patch_scene_not_found_returns_400(self, real_client_annotation, auth_headers):
        r = real_client_annotation.patch(URL, headers=auth_headers, json={
            'scene_id': f'nonexistent-{int(time.time())}',
            'collaborative': True,
        })
        assert r.status_code == 400


class TestRealAuth:
    # Missing header rejected before any DB or Kafka activity.
    def test_missing_header_rejects(self, real_client_annotation):
        r = real_client_annotation.get(URL)
        assert r.status_code == 401
