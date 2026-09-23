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
  wizard.form={querySelectorAll:()=>[{querySelector:s=>s==='[name=action]'?action:number}]};
  wizard.renumberSteps();assert.equal(action.value,'edit_email:1');assert.equal(number.textContent,2);
});
