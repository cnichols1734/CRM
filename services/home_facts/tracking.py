"""Attribute signed provider events to a stored approved send, never an org hint."""
from uuid import uuid5, NAMESPACE_URL
from itsdangerous import BadSignature

from models import db,TransactionCommunication,HomeFactsEvent
from services.home_facts.access import serializer,set_org


def apply_event(item):
    token=item.get('home_facts_receipt') or (item.get('unique_args') or {}).get('home_facts_receipt')
    try:
        receipt=serializer().loads(token,max_age=90*86400)
        org_id=int(receipt['org']);communication_id=int(receipt['communication'])
    except (BadSignature,ValueError,TypeError,KeyError):
        return False
    set_org(org_id)
    comm=TransactionCommunication.query.filter_by(id=communication_id,organization_id=org_id,
        communication_type='home_facts').with_for_update().first()
    provider_id=item.get('sg_message_id')
    if (not comm or not comm.provider_message_id or not isinstance(provider_id,str)
            or provider_id.split('.')[0]!=comm.provider_message_id.split('.')[0]):
        return False
    kind=item.get('event');provider_event=item.get('sg_event_id')
    if kind not in ('delivered','bounce','dropped','deferred','open','click') or not isinstance(provider_event,str) or not 1<=len(provider_event)<=300:
        return False
    ident=str(uuid5(NAMESPACE_URL,f'home-facts:{org_id}:{provider_event}'))
    if HomeFactsEvent.query.filter_by(id=ident,organization_id=org_id).first():
        return True
    response_id=(comm.communication_metadata or {}).get('home_facts_id')
    if not response_id:return False
    db.session.add(HomeFactsEvent(id=ident,organization_id=org_id,response_id=response_id,kind=kind,
        actor_kind='provider',details={'communication_id':comm.id,'recipient':comm.recipients[0],
        'possibly_automated':kind in ('open','click'),'provider_timestamp':item.get('timestamp')}))
    # Keep acceptance immutable. Delivery/bounce chronology lives in events so
    # an out-of-order callback cannot turn a bounce into a successful send.
    return True
