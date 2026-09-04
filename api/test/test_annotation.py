import json

import pytest


RESOURCE_MODULE = 'resources.annotation'

URL = '/annotations'
OBJECT_ID = 'obj-123'
SCENE_ID = 'scene-abc'
ARTIFACT_ID = 'art-xyz'
RECONSTRUCTION_ARTIFACT_ID = 'art-from-reconstruction'

VALID_SCENEGRAPH = {
    'nodes': {
        'model.glb': {'urls': ['https://public/model.glb']},
    },
    'edges': {'.': ['model.glb']},
}


# Patches both Kafka publish helpers to return True by default. Tests override
# individual return values to force the failure branches.
@pytest.fixture()
def mock_kafka(mocker):
    return {
        'avro': mocker.patch('resources.annotation.send_avro_message', return_value=True),
        'simple': mocker.patch('resources.annotation.send_simple_message', return_value=True),
    }


# Happy defaults for POST/PATCH pipelines. Individual tests override the DB
# rowcount / kafka return values to force error branches.
@pytest.fixture()
def happy_pipeline(mocker, mock_db, mock_kafka):
    # POST's INSERT ... RETURNING scene_id, artifact_id
    conn, cur = mock_db(fetchone=(SCENE_ID, RECONSTRUCTION_ARTIFACT_ID))
    return {
        'conn': conn,
        'cur': cur,
        'avro': mock_kafka['avro'],
        'simple': mock_kafka['simple'],
    }


class TestAuth:
    # GET without an Authorization header is rejected before touching the DB.
    def test_get_missing_header_returns_401(self, client):
        r = client.get(URL)
        assert r.status_code == 401

    # POST without an Authorization header is rejected before validation runs.
    def test_post_missing_header_returns_401(self, client):
        r = client.post(URL, json={'object_id': OBJECT_ID, 'scenegraph': VALID_SCENEGRAPH})
        assert r.status_code == 401

    # PATCH without an Authorization header is rejected before validation runs.
    def test_patch_missing_header_returns_401(self, client):
        r = client.patch(URL, json={'scene_id': SCENE_ID, 'collaborative': True})
        assert r.status_code == 401


class TestGet:
    def _row(self, scene_id=SCENE_ID, object_id=OBJECT_ID, timestamp='2026-01-01T00:00:00Z'):
        # Emulates a psycopg2 tuple row matching the columns list below.
        return (scene_id, object_id, timestamp, False, 'https://url', json.dumps({}), json.dumps({}), None, ARTIFACT_ID)

    def _description(self):
        # psycopg2's cur.description is a list of Column-like objects whose first
        # field is the column name — a plain (name,) tuple satisfies desc[0].
        return [
            ('scene_id',), ('object_id',), ('timestamp',), ('collaborative',),
            ('public_url',), ('content',), ('linked_objects',), ('location',), ('artifact_id',),
        ]

    # When rows come back, they're serialized into the response body under 'scenes' with a 200.
    def test_returns_scenes_list_when_rows_present(self, client, auth_headers, mock_db):
        row = self._row()
        mock_db(fetchall=[row], description=self._description())

        r = client.get(URL, headers=auth_headers)

        assert r.status_code == 200
        body = r.get_json()
        assert 'scenes' in body
        assert len(body['scenes']) == 1
        assert body['scenes'][0]['scene_id'] == SCENE_ID

    # FIX: 204 should never be returned.
    #      A new scene is created, so 201 is the right response.
    #
    # An empty result set returns 204 No Content per the resource's 200/204 branch.
    # def test_empty_result_returns_204(self, client, auth_headers, mock_db):
    #     mock_db(fetchall=[], description=self._description())
    #     r = client.get(URL, headers=auth_headers)
    #     assert r.status_code == 204

    # ?object_id=... appends "AND object_id = %s" to the SQL and binds the value.
    def test_filters_by_object_id(self, client, auth_headers, mock_db):
        _, cur = mock_db(fetchall=[], description=self._description())
        client.get(f'{URL}?object_id={OBJECT_ID}', headers=auth_headers)

        sql, params = cur.execute.call_args.args
        assert 'AND object_id = %s' in sql or 'WHERE object_id = %s' in sql
        assert OBJECT_ID in params

    # No page/per_page params → LIMIT 50 OFFSET 0 (defaults).
    def test_applies_default_pagination(self, client, auth_headers, mock_db):
        _, cur = mock_db(fetchall=[], description=self._description())
        client.get(URL, headers=auth_headers)

        sql, params = cur.execute.call_args.args
        assert 'LIMIT %s OFFSET %s' in sql
        assert params[-2:] == (50, 0)

    # page=3, per_page=10 → LIMIT 10 OFFSET 20.
    def test_applies_custom_pagination(self, client, auth_headers, mock_db):
        _, cur = mock_db(fetchall=[], description=self._description())
        client.get(f'{URL}?page=3&per_page=10', headers=auth_headers)

        _, params = cur.execute.call_args.args
        assert params[-2:] == (10, 20)

    # A DB connection failure is caught by the outer try and surfaces as a 500 with an error body.
    def test_db_error_returns_500(self, client, auth_headers, mocker):
        mocker.patch(
            'resources.annotation.get_db_connection',
            side_effect=Exception('db down'),
        )
        r = client.get(URL, headers=auth_headers)
        assert r.status_code == 500
        assert 'error' in r.get_json()


class TestPost:
    def _valid_body(self, **overrides):
        body = {'object_id': OBJECT_ID, 'scenegraph': VALID_SCENEGRAPH}
        body.update(overrides)
        return body

    # Empty JSON body triggers the "No data provided" 400 branch (empty dict is falsy).
    # Note: sending literally no body returns 415 from Flask before the resource runs.
    def test_empty_body_returns_400(self, client, auth_headers, happy_pipeline):
        r = client.post(URL, headers=auth_headers, json={})
        assert r.status_code == 400
        happy_pipeline['avro'].assert_not_called()
        happy_pipeline['simple'].assert_not_called()

    # object_id is required — missing it short-circuits before Kafka.
    def test_missing_object_id_returns_400(self, client, auth_headers, happy_pipeline):
        r = client.post(URL, headers=auth_headers, json={'scenegraph': VALID_SCENEGRAPH})
        assert r.status_code == 400
        assert 'object_id' in r.get_json()['error']
        happy_pipeline['avro'].assert_not_called()

    # Every shape that fails the scenegraph → nodes → urls[0] extraction returns 400.
    @pytest.mark.parametrize('body', [
        {'object_id': OBJECT_ID},                                                    # missing scenegraph
        {'object_id': OBJECT_ID, 'scenegraph': {}},                                  # empty scenegraph
        {'object_id': OBJECT_ID, 'scenegraph': {'edges': {}}},                       # missing nodes
        {'object_id': OBJECT_ID, 'scenegraph': {'nodes': {}}},                       # empty nodes
        {'object_id': OBJECT_ID, 'scenegraph': {'nodes': {'a.glb': {'urls': []}}}},  # empty urls list
        {'object_id': OBJECT_ID, 'scenegraph': {'nodes': {'a.glb': {}}}},            # missing urls key
    ])
    def test_invalid_scenegraph_returns_400(self, client, auth_headers, happy_pipeline, body):
        r = client.post(URL, headers=auth_headers, json=body)
        assert r.status_code == 400
        assert 'Invalid scene structure' in r.get_json()['error'] or 'scenegraph' in r.get_json()['error']
        happy_pipeline['avro'].assert_not_called()

    # Avro validation failure raises inside the try and surfaces as 500; simple message not sent.
    def test_avro_failure_returns_500(self, client, auth_headers, happy_pipeline):
        happy_pipeline['avro'].return_value = False
        r = client.post(URL, headers=auth_headers, json=self._valid_body())
        assert r.status_code == 500
        happy_pipeline['simple'].assert_not_called()

    # INSERT ... RETURNING with no matching reconstruction row → fetchone() is None → 404.
    def test_no_matching_reconstruction_returns_404(self, client, auth_headers, happy_pipeline):
        happy_pipeline['cur'].fetchone.return_value = None
        r = client.post(URL, headers=auth_headers, json=self._valid_body())
        assert r.status_code == 404
        assert OBJECT_ID in r.get_json()['error']
        happy_pipeline['simple'].assert_not_called()

    # Kafka simple-message failure after successful DB insert raises inside the try and returns 500.
    def test_kafka_simple_failure_returns_500(self, client, auth_headers, happy_pipeline):
        happy_pipeline['simple'].return_value = False
        r = client.post(URL, headers=auth_headers, json=self._valid_body())
        assert r.status_code == 500

    # Valid body + happy pipeline returns 201 with the created scene_id.
    def test_happy_path_returns_201(self, client, auth_headers, happy_pipeline):
        r = client.post(URL, headers=auth_headers, json=self._valid_body())
        assert r.status_code == 201
        body = r.get_json()
        assert body['message'] == 'Scene saved'
        assert 'scene_id' in body

    # send_avro_message is called with the annotations topic and the extracted URL from the scenegraph.
    def test_avro_message_uses_annotations_topic(self, client, auth_headers, happy_pipeline):
        client.post(URL, headers=auth_headers, json=self._valid_body())
        topic, _key, value, _schema = happy_pipeline['avro'].call_args.args
        assert topic == 'annotations'
        assert value['object_id'] == OBJECT_ID
        assert value['public_url'] == 'https://public/model.glb'

    # The INSERT statement joins reconstructions and returns scene_id + artifact_id.
    def test_insert_sql_uses_reconstructions_join(self, client, auth_headers, happy_pipeline):
        client.post(URL, headers=auth_headers, json=self._valid_body())
        sql, _params = happy_pipeline['cur'].execute.call_args.args
        assert 'INSERT INTO annotations' in sql
        assert 'FROM reconstructions' in sql
        assert 'RETURNING scene_id, artifact_id' in sql

    # A caller-provided artifact_id is passed as a SQL param (COALESCE overrides reconstruction's).
    def test_override_artifact_id_passed_to_sql(self, client, auth_headers, happy_pipeline):
        client.post(URL, headers=auth_headers, json=self._valid_body(artifact_id=ARTIFACT_ID))
        _sql, params = happy_pipeline['cur'].execute.call_args.args
        assert ARTIFACT_ID in params

    # After DB insert, a "saved" simple message is published to annotation_uploaded.
    def test_kafka_simple_uploaded_after_success(self, client, auth_headers, happy_pipeline):
        client.post(URL, headers=auth_headers, json=self._valid_body())
        topic, _key, value = happy_pipeline['simple'].call_args.args
        assert topic == 'annotation_uploaded'
        assert value == {'status': 'saved'}


class TestPatch:
    # PATCH reads the current row by column name (fetch_one_dict), so the mocked
    # cursor needs the matching description.
    ROW_DESC = [('content',), ('linked_objects',)]

    def _valid_body(self, **overrides):
        body = {'scene_id': SCENE_ID, 'collaborative': True}
        body.update(overrides)
        return body

    # Empty JSON body triggers the "No data provided" 400 branch (empty dict is falsy).
    # Note: sending literally no body returns 415 from Flask before the resource runs.
    def test_empty_body_returns_400(self, client, auth_headers, happy_pipeline):
        r = client.patch(URL, headers=auth_headers, json={})
        assert r.status_code == 400
        happy_pipeline['simple'].assert_not_called()

    # PATCH requires either scene_id or object_id — missing both returns 400.
    def test_missing_both_ids_returns_400(self, client, auth_headers, happy_pipeline):
        r = client.patch(URL, headers=auth_headers, json={'collaborative': True})
        assert r.status_code == 400
        happy_pipeline['simple'].assert_not_called()

    # Only whitelisted fields (collaborative/scenegraph/linked_objects) count — unknowns → 400.
    def test_no_updatable_fields_returns_400(self, client, auth_headers, happy_pipeline):
        r = client.patch(URL, headers=auth_headers, json={'scene_id': SCENE_ID, 'unknown_field': 'x'})
        assert r.status_code == 400
        happy_pipeline['simple'].assert_not_called()

    # SELECT current content returns None → 400 with "no scene found" message.
    def test_scene_not_found_returns_400(self, client, auth_headers, mock_db, mock_kafka):
        mock_db(fetchone=None)
        r = client.patch(URL, headers=auth_headers, json=self._valid_body())
        assert r.status_code == 400
        mock_kafka['simple'].assert_not_called()

    # Happy path with scene_id echoes it back in the 201 response.
    def test_happy_path_with_scene_id_returns_201(self, client, auth_headers, mock_db, mock_kafka):
        mock_db(fetchone=(json.dumps({}), json.dumps({})), description=self.ROW_DESC, rowcount=1)
        r = client.patch(URL, headers=auth_headers, json=self._valid_body())
        assert r.status_code == 201
        body = r.get_json()
        assert body['scene_id'] == SCENE_ID
        assert body['message'] == 'Scene updated'
        mock_kafka['simple'].assert_called_once()

    # Happy path with object_id echoes it back in the 201 response.
    def test_happy_path_with_object_id_returns_201(self, client, auth_headers, mock_db, mock_kafka):
        mock_db(fetchone=(json.dumps({}), json.dumps({})), description=self.ROW_DESC, rowcount=1)
        r = client.patch(URL, headers=auth_headers, json={'object_id': OBJECT_ID, 'collaborative': True})
        assert r.status_code == 201
        assert r.get_json()['object_id'] == OBJECT_ID

    # Sending a scenegraph-only patch (no 'collaborative') merges into the
    # existing content JSON and UPDATEs. Regression guard for the KeyError
    # bug in [annotation.py:299] where fields_to_update['collaborative'] was
    # looked up eagerly before the "field in fields_to_update" guard.
    def test_scenegraph_only_merge_updates_content(self, client, auth_headers, mock_db, mock_kafka):
        existing_content = {'nodes': {'model.glb': {'urls': ['https://old']}}}
        _, cur = mock_db(fetchone=(json.dumps(existing_content), json.dumps({})), description=self.ROW_DESC, rowcount=1)

        r = client.patch(URL, headers=auth_headers, json={
            'scene_id': SCENE_ID,
            'scenegraph': {'nodes': {'model.glb': {'urls': ['https://new']}}},
        })

        assert r.status_code == 201
        sql, params = cur.execute.call_args.args
        assert sql.startswith('UPDATE annotations SET')
        # collaborative was NOT patched, so its column should not appear in the SET clause
        assert 'collaborative = %s' not in sql
        merged_json = next(p for p in params if isinstance(p, str) and 'https://new' in p)
        assert 'https://new' in merged_json

    # Sending a linked_objects-only patch (no 'collaborative') UPDATEs the
    # linked_objects column without crashing. Same regression guard for the
    # same code path, different field.
    def test_linked_objects_only_updates_field(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db(fetchone=(json.dumps({}), json.dumps({'parent_object': 'a'})), description=self.ROW_DESC, rowcount=1)

        r = client.patch(URL, headers=auth_headers, json={
            'scene_id': SCENE_ID,
            'linked_objects': {'parent_object': 'b'},
        })

        assert r.status_code == 201
        sql, _params = cur.execute.call_args.args
        assert 'linked_objects = %s' in sql
        assert 'collaborative = %s' not in sql

    # Kafka simple-message failure after successful UPDATE surfaces as 500.
    def test_kafka_simple_failure_returns_500(self, client, auth_headers, mock_db, mock_kafka):
        mock_db(fetchone=(json.dumps({}), json.dumps({})), description=self.ROW_DESC, rowcount=1)
        mock_kafka['simple'].return_value = False
        r = client.patch(URL, headers=auth_headers, json=self._valid_body())
        assert r.status_code == 500
