"""Build the versioned field manifest and repair the supplied AcroForm metadata.

Run with the untouched stakeholder PDF as the first argument. The explicit
widget mapping below is tied to that template; unknown or missing fields fail.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject, TextStringObject


def slug(value):
    return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')


def build_manifest(reader):
    widgets = {}
    for page_number, page in enumerate(reader.pages, 1):
        for ref in page.get('/Annots', []):
            widget = ref.get_object()
            if widget.get('/Subtype') == '/Widget':
                widgets[str(widget['/T'])] = {
                    'page': page_number, 'rect': [float(v) for v in widget['/Rect']],
                    'type': str(widget['/FT']),
                }
    sections = []
    used = set()
    current = None
    group = None

    def section(key, title, help_text=''):
        nonlocal current
        current = {'id': key, 'title': title, 'help': help_text, 'groups': []}
        sections.append(current)

    def start(key, title, repeat=False):
        nonlocal group
        group = {'id': key, 'title': title, 'fields': [], 'optional_row': repeat}
        current['groups'].append(group)

    def field(key, label, numbers, options=None, multi=False, audience=None):
        physical = []
        for idx, number in enumerate(numbers):
            old = ('c' if options else 't') + str(number)
            if old not in widgets or old in used:
                raise ValueError(f'Missing or duplicated widget {old}')
            used.add(old)
            suffix = '__' + slug(options[idx]) if options else ''
            physical.append({'source_name': old, 'name': key + suffix,
                             **widgets[old], 'option': options[idx] if options else None})
        item = {'id': key, 'label': label, 'kind': 'multi' if multi else 'choice' if options else 'text',
                'widgets': physical, 'options': options or [], 'required': False,
                'audience': audience or ['appraiser', 'buyer'],
                'multiline': not options and physical[0]['rect'][3] - physical[0]['rect'][1] > 25,
                'max_length': 10000 if not options else None}
        group['fields'].append(item)

    def txt(number, key, label, **kw):
        field(key, label, [number], **kw)

    def choice(number, key, label, options=('Yes', 'No'), **kw):
        field(key, label, list(range(number, number + len(options))), list(options), **kw)

    section('identity', 'Your home', 'Answer what you know. Approximate dates are fine. Leave anything blank or write unsure. Your answers do not replace the Seller\'s Disclosure Notice.')
    start('identity', 'Property and seller')
    for n, key, label in [(1,'property_address','Property address'),(2,'city','City'),(3,'zip','ZIP'),(4,'seller_names','Seller name(s)'),(5,'seller_phone','Phone'),(6,'seller_email','Email'),(7,'origen_agent','Origen agent')]:
        txt(n, key, label, audience=['private'] if n in (4,5,6) else None)

    section('documents', 'Documents to gather', 'Phone photos and screenshots are fine. Having a document does not upload it. Add files separately. If you cannot find something, choose Can\'t locate.')
    docs = ['Utility bills, last 12 months','Survey and prior T-47 affidavit','Receipts / invoices for upgrades','Warranties','Appliance and equipment manuals','Permits','HOA documents','Elevation certificate','Flood and homeowner insurance declarations','Septic / aerobic maintenance contract','Termite bond / treatment records','Generator, pool or other service contracts','Solar lease, PPA or finance agreement','Inspection reports']
    for i, title in enumerate(docs):
        key = 'documents_' + str(i+1)
        start(key, title)
        choice(8+i*2, key, 'Can you provide this?', ['Have it', "Can't locate"], audience=['private'])

    section('basics', 'Home basics and utilities')
    start('basics', 'About the property')
    for n,k,l in [(36,'year_purchased','Year you purchased'),(37,'year_built','Year built, if known'),(38,'lot_size','Approximate lot size'),(39,'stories','Stories')]: txt(n,k,l)
    start('hoa', 'HOA and districts')
    choice(40,'hoa','HOA?')
    for n,k,l in [(42,'hoa_name','HOA name'),(43,'hoa_dues','Dues, dollars and how often'),(44,'hoa_contact','Management company / contact')]: txt(n,k,l)
    choice(45,'district','MUD or PID?', ['MUD','PID','Both','Neither','Unsure'])
    txt(50,'district_names','District name(s)'); txt(51,'district_tax','Approximate annual MUD/PID tax or assessment')
    start('water_gas','Water, sewer and gas')
    choice(52,'water_source','Water source',['City / utility','Water co-op','Private well','Shared well'])
    txt(56,'water_other','Other water source')
    choice(57,'sewer','Sewer',['City sewer','Conventional septic','Aerobic septic'])
    txt(60,'sewer_other','Other sewer')
    choice(61,'gas','Gas',['Natural gas','Propane','None (all electric)'])
    choice(64,'propane_tank','Propane tank',['Owned','Leased','N/A'])
    txt(67,'propane_supplier','Propane tank size in gallons and supplier')
    for i,title in enumerate(['Electric','Gas / propane','Water','Sewer','Trash','Internet']):
        key='utility_'+slug(title); start(key,title)
        txt(68+i*2,key+'_provider','Provider / company'); txt(69+i*2,key+'_cost','Average monthly cost, approximate')
    start('utility_other','Other utility',True)
    for n,k,l in [(80,'utility_other_name','Utility'),(81,'utility_other_provider','Provider / company'),(82,'utility_other_cost','Average monthly cost, approximate')]: txt(n,k,l)
    start('internet','Internet options'); txt(83,'internet_options','Internet options available at the address, fiber, cable, etc.')
    start('security','Security system')
    choice(84,'security','Do you have a security system?')
    choice(86,'security_ownership','Is the system owned or leased?',['Owned','Leased'])
    choice(88,'security_monitoring','Is the system monitored?',['Monitored','Unmonitored'])
    txt(90,'security_company','Security company / contract end date'); txt(91,'security_fee','Monthly fee, if any')
    choice(92,'security_conveys','Security equipment',['Staying with the house','Seller is taking it','Some of each'])
    txt(95,'security_notes','Which equipment stays or goes: panels, cameras, doorbell, sensors, smart locks')

    section('showings','Showings', 'These answers are for your agent. Share alarm codes directly by phone or text rather than writing them here.')
    start('showings','Showing availability')
    choice(96,'showing_notice','Do you need advance notice before showings?',['No, show anytime','Yes'],audience=['private'])
    txt(98,'showing_notice_time','If yes, how much notice?',audience=['private'])
    choice(99,'showing_alarm','Will an alarm be set during showings?',audience=['private'])
    choice(101,'showing_code_needed','Do showing agents need an alarm code?',audience=['private'])
    start('pets','Pets and showing notes')
    choice(103,'pets','Do you have pets?',audience=['private'])
    txt(105,'pets_type','Type and how many',audience=['private']); txt(106,'pets_location','Where will they be during showings?',audience=['private'])
    choice(107,'pets_home','Will pets be home during showings?',['Yes','No','Sometimes'],audience=['private'])
    txt(110,'showing_notes','Anything else we need to know for showings? Gate codes, parking, livestock, areas off limits, work-from-home hours.',audience=['private'])

    section('conveys','What stays and what goes','Items permanently attached generally stay unless excluded. This list helps us plan; final terms are set in the sales contract.')
    items=['Curtains, drapes & rods','Blinds & shades','Shutters / awnings','Ceiling fans','Light fixtures / chandeliers','Mirrors fixed in place','Mounted TVs & brackets','Built-in speakers / sound system','Satellite dish / antenna','Window AC units','Generator','Propane tank','Water softener / filtration','Garage door openers & remotes','Garage shelving / workbench','Storage shed(s)','Outdoor kitchen / grill (built-in)','Pool equipment & cleaner','Hot tub / portable spa','Playset / swing set','Basketball goal','Landscape & yard lighting','Fountains / statues / bird baths','Plants & shrubbery (potted)','Fireplace screen / gas logs','Bookshelves (attached)','Security cameras / doorbell','Smart locks / thermostats','Appliance manuals']
    for i,title in enumerate(items):
        key='conveys_'+slug(title); n=111+i*4; start(key,title)
        choice(n,key,'Stays or goes?',['Stays','Goes','N/A']); txt(n+3,key+'_notes','Notes, which ones and where')
    for i in range(4):
        n=227+i*5; key='conveys_other_'+str(i+1); start(key,'Other item '+str(i+1),True)
        txt(n,key+'_name','Item'); choice(n+1,key,'Stays or goes?',['Stays','Goes','N/A']); txt(n+4,key+'_notes','Notes')

    section('systems','Systems and appliances','Use the year installed or replaced rather than an age. Approximate dates and unsure are fine.')
    systems=['Roof','AC / HVAC unit 1','AC / HVAC unit 2','AC / HVAC unit 3','Heater / furnace','Water heater 1','Water heater 2','Water softener','Water filtration','Septic / aerobic system','Well pump / pressure tank','Electrical panel','Pool / spa pump','Pool / spa heater','Salt chlorinator','Garage door opener(s)','Sprinkler system','Exterior paint','Interior paint']
    for i,title in enumerate(systems):
        key='system_'+slug(title); n=247+i*5; start(key,title)
        txt(n,key+'_year','Year installed'); txt(n+1,key+'_type','Type / brand / size'); choice(n+2,key+'_warranty','Under warranty?'); txt(n+4,key+'_notes','Notes')
    for i in range(2):
        n=342+i*6; key='system_other_'+str(i+1); start(key,'Other system '+str(i+1),True)
        txt(n,key+'_name','System'); txt(n+1,key+'_year','Year installed'); txt(n+2,key+'_type','Type / brand / size'); choice(n+3,key+'_warranty','Under warranty?'); txt(n+5,key+'_notes','Notes')
    start('roof','Roof material and water heater')
    choice(354,'roof_material','Roof material',['Composition','Metal','Tile','Flat / TPO'],multi=True)
    txt(358,'roof_material_other','Other roof material')
    choice(359,'roof_impact','Impact-resistant, Class 4?',['Yes','No','Unsure'])
    choice(362,'water_heater_type','Water heater type',['Tank, gas','Tank, electric','Tankless, gas','Tankless, electric'],multi=True)
    appliances=['Refrigerator','2nd refrigerator','Range / oven','Cooktop','Wall oven(s)','Dishwasher','Microwave','Wine / beverage fridge','Ice maker','Washer','Dryer','Freezer']
    for i,title in enumerate(appliances):
        key='appliance_'+slug(title); n=366+i*5; start(key,title)
        txt(n,key+'_year','Year'); txt(n+1,key+'_model','Brand / model'); choice(n+2,key+'_conveys','Stays or goes?',['Stays','Goes w/ seller','N/A'])
    for i in range(2):
        key='appliance_other_'+str(i+1); n=426+i*6; start(key,'Other appliance '+str(i+1),True)
        txt(n,key+'_name','Appliance'); txt(n+1,key+'_year','Year'); txt(n+2,key+'_model','Brand / model'); choice(n+3,key+'_conveys','Stays or goes?',['Stays','Goes w/ seller','N/A'])

    section('improvements','Upgrades and improvements','Be specific about brand, material, size and scope. Costs are optional and go only to the appraiser. For added rooms or converted space, describe heating/cooling and permits; do not estimate square footage.')
    for i in range(24):
        key='improvement_'+str(i+1); n=438+i*9; start(key,'Improvement '+str(i+1),True)
        txt(n,key+'_year','Year'); txt(n+1,key+'_description','Improvement, be specific'); txt(n+2,key+'_cost','Approximate cost',audience=['appraiser'])
        choice(n+3,key+'_by','Done by',['Pro','DIY']); choice(n+5,key+'_permit','Permit?',['Yes','No'])
        choice(n+7,key+'_warranty','Warranty?',['Yes','Transferable'],multi=True)
    start('hidden','Hidden improvements'); txt(654,'hidden_improvements','Anything an appraiser or buyer would probably miss? Hidden upgrades, behind-the-wall work, quality of materials.')

    section('maintenance','Recent maintenance and service','Include anything from the last few years.')
    maintenance=['HVAC service / tune-up','Septic pumped','Aerobic maintenance contract','Termite inspection / treatment','Roof inspection / repair','Foundation inspection','Plumbing','Electrical / panel inspection','Generator service','Pool service','Gutter cleaning','Pest control']
    for i,title in enumerate(maintenance):
        key='maintenance_'+slug(title); n=655+i*3; start(key,title)
        txt(n,key+'_date','Date, month/year'); txt(n+1,key+'_company','Company'); txt(n+2,key+'_notes','Notes / contract?')
    for i in range(4):
        key='maintenance_other_'+str(i+1); n=691+i*4; start(key,'Other service '+str(i+1),True)
        txt(n,key+'_name','Service'); txt(n+1,key+'_date','Date, month/year'); txt(n+2,key+'_company','Company'); txt(n+3,key+'_notes','Notes / contract?')
    start('warranties','Termite and foundation warranties')
    choice(707,'termite_warranty','Termite bond or warranty?',['Yes, transferable','Yes, not transferable','No','Unsure'])
    choice(711,'foundation_warranty','Foundation repair warranty?',['Yes, transferable','Yes, not transferable','No repairs'])

    section('energy','Energy and big-ticket items')
    start('solar','Solar panels')
    choice(714,'solar','Solar panels',['None','Owned, paid off','Owned, financed','Leased / PPA'])
    for n,k,l in [(718,'solar_company','Solar company'),(719,'solar_year_size','Year installed / system size, kW'),(720,'solar_payment','Monthly payment, if any')]: txt(n,k,l)
    start('generator','Generator')
    choice(721,'generator','Generator',['None','Whole-home (permanent)','Portable','Transfer switch only'])
    for n,k,l in [(725,'generator_type','Brand / kW / fuel'),(726,'generator_year','Year installed'),(727,'generator_contract','Maintenance contract, company and cost')]: txt(n,k,l)
    start('insulation','Insulation')
    choice(728,'insulation','Insulation',['Spray foam (open)','Spray foam (closed)','Blown-in added','Original'],multi=True)
    for n,k,l in [(732,'insulation_other','Other insulation'),(733,'insulation_year','Year insulation added / R-value, if known'),(734,'radiant_barrier','Radiant barrier? Year')]: txt(n,k,l)
    start('windows','Windows')
    choice(735,'windows','Windows',['Original','Double pane','Low-E','Impact'],multi=True)
    for n,k,l in [(739,'windows_other','Other windows'),(740,'windows_year','Year windows replaced / brand'),(741,'windows_warranty','Window or door warranty?')]: txt(n,k,l)
    start('technology','Water softener, EV charging and technology')
    choice(742,'softener','Water softener',['None','Owned','Leased'])
    choice(745,'ev_charger','EV charger',['None','Level 2 (240V)','Outlet only'])
    txt(748,'ev_other','Other EV charging'); txt(749,'smart_technology','Smart thermostats, lighting, whole-home Wi-Fi, other technology that stays')

    section('insurance','Flood, insurance and survey','Premiums are optional. Past flooding, water penetration and insurance claims belong on your Seller\'s Disclosure Notice.')
    start('flood','Flood insurance')
    for n,k,l in [(750,'flood_zone','FEMA flood zone, if known'),(751,'elevation_certificate','Elevation certificate?'),(752,'elevation_year','Year of elevation certificate')]: txt(n,k,l)
    choice(753,'flood_insurance','Do you carry flood insurance?')
    for n,k,l in [(755,'flood_carrier','Flood carrier'),(756,'flood_premium','Approximate annual flood premium'),(757,'flood_coverage','Coverage amount'),(758,'home_insurance_carrier','Homeowner insurance carrier'),(759,'home_insurance_premium','Approximate annual premium, optional')]: txt(n,k,l)
    choice(760,'insurance_discounts','Any wind/hail or roof insurance discounts?',['Yes','No','Unsure'])
    start('survey','Survey')
    choice(763,'survey','Do you have a survey of the property?',['Yes','No','Unsure'])
    for n,k,l in [(766,'survey_year','Year of survey'),(767,'survey_company','Surveyor / company'),(768,'survey_t47','Have you signed a T-47 before?')]: txt(n,k,l)
    choice(769,'survey_changed','Anything added or changed since the survey?')
    txt(771,'survey_changes','If yes, what and when? Fence, pool, garage, carport, shop, shed, addition, driveway, patio cover, slab, etc.')

    section('living','Living here','Keep it about the home and property. Leave out descriptions of neighbors or who lives in the area.')
    start('living','What makes this home special?')
    txt(772,'living_love','What do you love most about this home?')
    txt(773,'living_features','Best features a buyer might miss on a 20-minute showing, storage, pantry, natural light, views, etc.')
    for i,title in enumerate(['Lawn / landscaping','Pest control','HVAC','Pool','Plumber','Electrician','Handyman']):
        key='provider_'+slug(title); n=774+i*2; start(key,title)
        txt(n,key+'_name','Company / person'); txt(n+1,key+'_phone','Phone')
    start('provider_other','Other service provider',True)
    txt(788,'provider_other_service','Service'); txt(789,'provider_other_name','Company / person'); txt(790,'provider_other_phone','Phone')
    section('notes','Anything else?')
    start('notes','Anything else?'); txt(791,'other_notes','Anything else you want the appraiser or buyers to know?')
    if used != set(widgets):
        raise ValueError(f'Unmapped widgets: {set(widgets)-used}')
    return {'version': 1, 'sections': sections, 'widget_count': len(used),
            'acknowledgment': "My answers are based on my best knowledge and have not been verified by Origen Realty. They are not a warranty and do not replace the Seller's Disclosure Notice, inspections or the sales contract. I allow my agent to prepare a Property Brief and Home Guide from my answers and documents and review both with me before sharing with appraisers, lenders, buyers and their agents. I will tell my agent if anything changes."}


def main():
    source = Path(sys.argv[1])
    destination = Path(__file__).resolve().parents[1] / 'documents/home_facts/v1'
    destination.mkdir(parents=True, exist_ok=True)
    raw = source.read_bytes()
    reader = PdfReader(source)
    manifest = build_manifest(reader)
    manifest['source_sha256'] = hashlib.sha256(raw).hexdigest()
    (destination/'original.pdf').write_bytes(raw)
    writer = PdfWriter(clone_from=reader)
    mapping = {w['source_name']: (f, w) for s in manifest['sections'] for g in s['groups'] for f in g['fields'] for w in f['widgets']}
    for page in writer.pages:
        for ref in page['/Annots']:
            widget = ref.get_object()
            field, entry = mapping[str(widget['/T'])]
            widget[NameObject('/T')] = TextStringObject(entry['name'])
            widget[NameObject('/TU')] = TextStringObject(field['label'])
            widget[NameObject('/Ff')] = NumberObject(int(widget.get('/Ff', 0)) & ~2)
            if widget['/FT'] == '/Tx':
                widget[NameObject('/MaxLen')] = NumberObject(field['max_length'])
    target = destination/'questionnaire.pdf'
    writer.write(target)
    manifest['template_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
    (destination/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    reopened = PdfReader(target)
    assert len(reopened.get_fields()) == manifest['widget_count'] == 791
    assert set(reopened.get_fields()) == {w['name'] for _,w in mapping.values()}
    print(f'Prepared {len(mapping)} widgets in {len(manifest["sections"])} sections: {target}')


if __name__ == '__main__':
    main()
