import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {DraftRecovery} from '../../frontend/marketing_draft_recovery.js';

const source = await readFile(new URL('../../frontend/controllers/marketing_campaign_wizard_controller.js', import.meta.url), 'utf8');
const {default: Wizard} = await import('data:text/javascript;base64,' + Buffer.from(source.replace(/^import .*;$/gm, '') .replace('export default class extends Controller', 'export default class')).toString('base64'));

function fixture(saved, version='v1', restore=true) {
  const items = new Map(saved ? [['draft', JSON.stringify(saved)]] : []);
  globalThis.localStorage = {getItem:k=>items.get(k), setItem:(k,v)=>items.set(k,v), removeItem:k=>items.delete(k)};
  globalThis.window = {addEventListener(){}, removeEventListener(){}};
  globalThis.document = {querySelector:()=>null, createElement:()=>({})};
  const field = {name:'subject', value:'Server subject',type:'text'};
  const form = {elements:[field], append(el){this.elements.push(el);}, addEventListener(){}, removeEventListener(){}};
  const root = {dataset:{draftKey:'draft',draftVersion:version,draftRestore:String(restore)}, querySelector:()=>({})};
  return {root, form, items, field};
}
const saved = extra => ({at:Date.now(),version:'v1',fields:{subject:['Unsaved subject']},...extra});

test('failed submission keeps browser recovery until confirmed server save',()=>{
  const f=fixture();const recovery=new DraftRecovery(f.root,f.form);
  f.field.value='Latest subject';recovery.submitting();
  assert.equal(JSON.parse(f.items.get('draft')).fields.subject[0],'Latest subject');
  const again=fixture(JSON.parse(f.items.get('draft')));
  new DraftRecovery(again.root,again.form);
  assert.equal(again.field.value,'Latest subject');
});

test('a newer server revision prevents stale local edits replacing saved changes',()=>{
  const f=fixture(saved(),'v2');new DraftRecovery(f.root,f.form);
  assert.equal(f.field.value,'Server subject');assert.equal(f.items.has('draft'),false);
});

test('generated POST content is not replaced by previous browser recovery',()=>{
  const f=fixture(saved(),'v1',false);new DraftRecovery(f.root,f.form);
  assert.equal(f.field.value,'Server subject');assert.equal(f.items.has('draft'),true);
});

test('dynamic recipient identity is restored structurally, never positionally',()=>{
  const f=fixture(saved({fields:{subject:['New subject'],contact_id:['42']},structure:{contacts:[{id:'42',name:'Bob'}],steps:[{id:'300',wait:'30'}]}}));
  const oldChip={name:'contact_id',type:'hidden',value:'17'};f.form.elements.push(oldChip);
  let restored;
  new DraftRecovery(f.root,f.form,null,{excluded:['contact_id','step_wait'],restore:structure=>{restored=structure;}});
  assert.equal(oldChip.value,'17');assert.deepEqual(restored.contacts,[{id:'42',name:'Bob'}]);
  assert.deepEqual(restored.steps,[{id:'300',wait:'30'}]);
});

test('changing audience immediately invalidates an in-flight estimate',async()=>{
  const wizard=new Wizard();let finish;let aborted=false;
  wizard.form={elements:{}};wizard.recovery={save(){}};wizard.filterGroups=()=>{};wizard.updateSummary=()=>{};
  wizard.launchTarget={disabled:false};wizard.countTargets=[];wizard.filter=()=>({});
  wizard.request=()=>new Promise(resolve=>{finish=resolve;});
  const pending=wizard.estimate();
  wizard.estimateAbort.signal.addEventListener('abort',()=>{aborted=true;});
  wizard.changed({target:{name:'groups'}});
  assert.equal(aborted,true);assert.equal(wizard.launchTarget.disabled,true);
  finish({sendable:100});await pending;clearTimeout(wizard.timer);
  assert.equal(wizard.count,null);assert.equal(wizard.launchTarget.disabled,true);
});

test('remaining follow-up edit actions are renumbered after removal',()=>{
  const wizard=new Wizard();const action={value:'edit_email:2'},number={textContent:'3'};
  wizard.form={querySelectorAll:()=>[{querySelector:s=>s==='[name=action]'?action:s==='.mkt-sequence-number'?number:null}]};
  wizard.renumberSteps();assert.equal(action.value,'edit_email:1');assert.equal(number.textContent,2);
});

test('native submission keeps the action and prevents duplicate submissions',()=>{
  const wizard=new Wizard();let saved=0;
  wizard.form={inert:false,setAttribute(){}};wizard.recovery={submitting(){saved++;}};
  wizard.launchTarget={disabled:false};
  const submitter={value:'launch',disabled:false};
  const event={submitter,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;}};
  wizard.submit(event);
  assert.equal(saved,1);assert.equal(wizard.form.inert,true);
  assert.equal(submitter.value,'launch');assert.equal(submitter.disabled,false);
  wizard.submit(event);assert.equal(event.defaultPrevented,true);assert.equal(saved,1);
});

test('failed recipient check exposes retry and clears stale recipients',async()=>{
  const wizard=new Wizard();let cleared=false;
  wizard.launchTarget={disabled:false};wizard.hasRetryTarget=true;wizard.retryTarget={hidden:true};
  wizard.countTargets=[];wizard.breakdownTargets=[{}];wizard.moreTargets=[{}];
  wizard.recipientsTargets=[{replaceChildren(){cleared=true;}}];
  wizard.filter=()=>({});wizard.request=async()=>{throw new Error('Offline');};wizard.updateSummary=()=>{};
  wizard.count=25;await wizard.estimate();
  assert.equal(wizard.count,null);assert.equal(wizard.retryTarget.hidden,false);
  assert.equal(cleared,true);assert.equal(wizard.launchTarget.disabled,true);
  let retried=false;wizard.estimate=()=>{retried=true;};wizard.retryEstimate();assert.equal(retried,true);
});

test('previewing a follow-up keeps the first subject and edits the selected email',async()=>{
  const wizard=new Wizard();wizard.form={elements:{template_id:{value:'first'}}};
  const items=[0,1].map(step=>({dataset:{step:String(step),templateId:step?'followup':'first'},attrs:{},setAttribute(k,v){this.attrs[k]=v;}}));
  wizard.hasPreviewItemTarget=true;wizard.previewItemTargets=items;
  wizard.hasEditPreviewTarget=true;wizard.editPreviewTarget={};
  wizard.hasPreviewPositionTarget=true;wizard.previewPositionTarget={};
  wizard.hasPreviewSubjectTarget=true;wizard.previewSubjectTarget={};wizard.previewTarget={};
  wizard.subjectTarget={textContent:'First subject'};wizard.updateSummary=()=>{};
  let requested;
  wizard.request=async(url,payload)=>{requested=payload.template_id;return {html:'<p>Follow-up</p>',subject:'Follow-up subject'};};
  globalThis.window={matchMedia:()=>({matches:false})};
  wizard.selectPreview({currentTarget:items[1]});await wizard.previewAbort&&new Promise(r=>setTimeout(r));
  assert.equal(requested,'followup');assert.equal(wizard.previewReady,true);
  assert.equal(wizard.editPreviewTarget.value,'edit_email:1');
  assert.equal(wizard.previewPositionTarget.textContent,'Email 2 of 2');
  assert.equal(items[1].attrs['aria-current'],'true');assert.equal(items[0].attrs['aria-current'],'false');
  assert.equal(wizard.subjectTarget.textContent,'First subject');
  assert.equal(wizard.previewSubjectTarget.textContent,'Follow-up subject');
});

test('send test uses the email selected for preview',async()=>{
  const wizard=new Wizard();wizard.form={elements:{template_id:{value:'first'},from_name:{value:'Alex'},reply_to:{value:'alex@example.com'}}};
  wizard.hasPreviewItemTarget=true;wizard.previewItemTargets=[{dataset:{templateId:'first'}},{dataset:{templateId:'followup'}}];
  wizard.currentStep=1;wizard.testStatusTarget={};
  let requested;wizard.request=async(url,payload)=>{requested=payload.template_id;return {sent:['alex@example.com']};};
  const button={disabled:false};await wizard.sendTest({currentTarget:button});
  assert.equal(requested,'followup');assert.equal(button.disabled,false);
  assert.match(wizard.testStatusTarget.textContent,/Test sent/);
});

test('preview and search controls can avoid marking a draft as edited',()=>{
  const f=fixture();const recovery=new DraftRecovery(f.root,f.form,null,{ignoreChange:event=>event.target.type==='search'});
  recovery.changed({target:{type:'search'}});
  assert.equal(recovery.dirty,undefined);assert.equal(f.items.has('draft'),false);
  f.field.value='Edited subject';recovery.changed({target:f.field});
  assert.equal(recovery.dirty,true);assert.equal(JSON.parse(f.items.get('draft')).fields.subject[0],'Edited subject');
});

test('a failed preview offers retry and keeps sending disabled', async()=>{
  const wizard=new Wizard();wizard.form={elements:{template_id:{value:'first'}}};
  wizard.hasPreviewRetryTarget=true;wizard.previewRetryTarget={hidden:true};
  wizard.hasPreviewSubjectTarget=true;wizard.previewSubjectTarget={};wizard.previewTarget={};
  wizard.request=async()=>{throw new Error('Offline');};wizard.updateSummary=()=>{};
  await wizard.preview();
  assert.equal(wizard.previewReady,false);assert.equal(wizard.previewRetryTarget.hidden,false);
  wizard.subjectTarget={};wizard.request=async()=>({html:'<p>Ready</p>',subject:'Ready'});
  await wizard.preview();
  assert.equal(wizard.previewReady,true);assert.equal(wizard.previewRetryTarget.hidden,true);
});

const librarySource = await readFile(new URL('../../frontend/controllers/marketing_template_library_controller.js', import.meta.url), 'utf8');
const {default: Library} = await import('data:text/javascript;base64,' + Buffer.from(librarySource.replace(/^import .*;$/gm, '').replace('export default class extends Controller', 'export default class')).toString('base64'));

test('template filtering shows one collection and restores cards after clearing search',()=>{
  const library=new Library();
  const collection=(category,names)=>{
    const cards=names.map(name=>({dataset:{templateName:name},hidden:false}));
    const empty={hidden:true};
    return {dataset:{category},cards,empty,querySelectorAll:()=>cards,querySelector:()=>empty};
  };
  const base=collection('base-templates',['open house','just sold']);
  const saved=collection('saved-templates',['my newsletter']);
  library.collectionTargets=[base,saved];library.categoryTargets=[];
  library.searchTarget={value:'newsletter'};library.selectedCategory='saved-templates';
  library.filter();assert.equal(base.hidden,true);assert.equal(saved.hidden,false);
  assert.equal(saved.cards[0].hidden,false);assert.equal(saved.empty.hidden,true);
  library.searchTarget.value='missing';library.filter();assert.equal(saved.empty.hidden,false);
  library.searchTarget.value='';library.filter();assert.equal(saved.cards[0].hidden,false);
  assert.equal(saved.empty.hidden,true);
});


test('scheduled times use AM/PM including noon and midnight',()=>{
  const wizard=new Wizard();
  assert.match(wizard.scheduleLabel('2026-10-01T00:00','Central time'), /12:00 AM · Central time/);
  assert.match(wizard.scheduleLabel('2026-10-01T12:00','Central time'), /12:00 PM · Central time/);
  assert.match(wizard.scheduleLabel('2026-10-01T17:45','Pacific time'), /5:45 PM · Pacific time/);
});

test('date and AM/PM time controls preserve the scheduled local wall time',()=>{
  const wizard=new Wizard();wizard.updateSummary=()=>{};
  wizard.form={elements:{schedule_date:{value:'2026-10-01'},schedule_time:{value:'17:30'},scheduled_at:{value:''}}};
  wizard.scheduleChanged();assert.equal(wizard.form.elements.scheduled_at.value,'2026-10-01T17:30');
  wizard.form.elements.schedule_date.value='';wizard.scheduleChanged();
  assert.equal(wizard.form.elements.scheduled_at.value,'');
});
