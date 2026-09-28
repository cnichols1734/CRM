from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest
import requests
from flask import Flask
from sqlalchemy import create_engine, inspect, text
from alembic.migration import MigrationContext
from alembic.operations import Operations

from models import db, Organization, ClientBrowseInquiry
from services import repliers_listings as listings
from tests.test_client_discovery import signup, auth, inquiry_body


@pytest.fixture
def record():
    return {'boardId': 110, 'mlsNumber': 'TEST123', 'listPrice': 510000,
        'address': {'streetNumber': '123', 'streetName': 'Oak', 'streetSuffix': 'Dr', 'city': 'Austin', 'state': 'TX', 'zip': '78701'},
        'permissions': dict.fromkeys(['displayPublic', 'displayInternetEntireListing', 'displayAddressOnInternet', 'displayOnMap'], 'Y'),
        'images': ['sample/test_1.jpg', 'sample/test_2.jpg'],
        'details': {'description': 'SAMPLE DATA. A home for testing.', 'numBedrooms': 3, 'numBathrooms': 2.5, 'sqft': '2,100', 'yearBuilt': '2018', 'propertyType': 'Residential', 'style': 'Single Family Residence', 'heating': 'Central'},
        'map': {'latitude': 30.267, 'longitude': -97.743},
        'taxes': {'annualAmount': 4100}, 'agents': [{'name': 'Sample Agent'}],
        'office': {'brokerageName': 'Sample Office'}}


@pytest.fixture
def provider(app, seed, monkeypatch, record):
    monkeypatch.setitem(app.config, 'REPLIERS_API_KEY', 'test-server-only-secret')
    listings._cache.clear()
    with app.app_context():
        for oid in [seed['org_a'], seed['org_b']]:
            db.session.get(Organization, oid).client_app_settings = {'enabled': True}
        db.session.commit()
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        class Response:
            status_code = 200
            def json(self):
                if url.endswith('/TEST123'):
                    return deepcopy(record)
                return {'listings': [deepcopy(record)], 'page': 1, 'numPages': 2, 'count': 25,
                    'aggregates': {'address': {'city': {'Austin': 25}}}}
        return Response()
    monkeypatch.setattr(listings.requests, 'get', get)
    yield calls
    listings._cache.clear()
    with app.app_context():
        for oid in [seed['org_a'], seed['org_b']]:
            db.session.get(Organization, oid).client_app_settings = None
        db.session.commit()


BASE = '/api/client/v1/discovery'


def test_public_search_normalizes_provider_photos_and_only_supplied_facts(client, provider):
    response = client.get(BASE + '/brokerages/test-realty-a/listings?min_beds=3&page=1')
    assert response.status_code == 200
    item = response.json['listings'][0]
    assert item['id'] == 'rp-110-TEST123' and item['sqft'] == 2100
    assert item['photo_names'] == ['https://cdn.repliers.io/sample/test_1.jpg', 'https://cdn.repliers.io/sample/test_2.jpg']
    assert item['rooms'] == [] and item['interior'] == []
    assert item['map_longitude'] == -97.743
    assert b'test-server-only-secret' not in response.data
    assert provider[0][1]['headers']['REPLIERS-API-KEY'] == 'test-server-only-secret'
    assert provider[0][1]['params']['minBeds'] == 3
    client.get(BASE + '/brokerages/test-realty-a/listings?min_beds=3&page=1')
    assert len(provider) == 1


def test_public_permissions_and_missing_map_are_respected(record):
    for flag in ('displayPublic', 'displayInternetEntireListing', 'displayAddressOnInternet'):
        private = deepcopy(record); private['permissions'][flag] = 'N'
        assert listings.normalize(private) is None
    record['permissions']['displayOnMap'] = 'N'
    assert listings.normalize(record)['map_latitude'] is None
    record['images'] = ['live/listing.jpg']; record['details']['description'] = 'Live listing'
    assert listings.normalize(record) is None


def test_invalid_search_and_ids_do_not_reach_provider(client, provider):
    for query in ('page=0', 'page=hello', 'max_price=-1', 'min_beds=99', 'property_type=secret'):
        assert client.get(BASE + '/brokerages/test-realty-a/listings?' + query).status_code == 400
    assert client.get(BASE + '/brokerages/test-realty-a/listings?ids=../../secret').status_code == 400
    assert client.get(BASE + '/brokerages/unknown/listings').status_code == 404
    assert provider == []


def test_failures_are_actionable_without_upstream_secrets(app, client, provider, monkeypatch):
    def fail(*args, **kwargs):
        raise requests.Timeout('test-server-only-secret')
    monkeypatch.setattr(listings.requests, 'get', fail)
    response = client.get(BASE + '/brokerages/test-realty-a/listings')
    assert response.status_code == 503 and b'test-server-only-secret' not in response.data
    monkeypatch.setitem(app.config, 'REPLIERS_API_KEY', None)
    assert client.get(BASE + '/brokerages/test-realty-a/listings').status_code == 503


def test_provider_favorites_and_inquiry_snapshot_flow(app, client, owner_a_client, provider):
    registered = signup(client)
    headers = auth(registered)
    assert client.put(BASE + '/saved', headers=headers, json={'changes': {'rp-110-TEST123': True}}).status_code == 200
    fetched = client.get(BASE + '/brokerages/test-realty-a/listings?ids=rp-110-TEST123')
    assert fetched.status_code == 200 and fetched.json['listings'][0]['id'] == 'rp-110-TEST123'
    inquiry = inquiry_body(); inquiry['listing_id'] = 'rp-110-TEST123'
    response = client.post(BASE + '/inquiries', headers=headers, json=inquiry)
    assert response.status_code == 201
    with app.app_context():
        saved = db.session.get(ClientBrowseInquiry, response.json['id'])
        assert saved.listing_snapshot['street'] == '123 Oak Dr'
    inbox = owner_a_client.get('/messages')
    assert b'123 Oak Dr, Austin' in inbox.data
    reply = client.get(BASE + '/inquiries', headers=headers)
    assert reply.json['inquiries'][0]['listing_snapshot']['source'] == 'repliers_sandbox'


def test_snapshot_migration_is_additive_and_repeatable():
    path = Path(__file__).parents[1] / 'migrations/versions/add_inquiry_listing_snapshot.py'
    spec = importlib.util.spec_from_file_location('inquiry_snapshot_migration', path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE client_browse_inquiries (id INTEGER PRIMARY KEY, body TEXT)'))
        connection.execute(text("INSERT INTO client_browse_inquiries VALUES (1, 'Keep this inquiry')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade(); migration.upgrade()
        assert 'listing_snapshot' in {c['name'] for c in inspect(connection).get_columns('client_browse_inquiries')}
        assert connection.execute(text('SELECT body FROM client_browse_inquiries')).scalar() == 'Keep this inquiry'
