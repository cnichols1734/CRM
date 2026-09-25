import { Controller } from '@hotwired/stimulus';
import { DraftRecovery } from '../marketing_draft_recovery';

export default class extends Controller {
  static values = {estimateUrl:String, contactsUrl:String, previewAsUrl:String, testUrl:String};
  static targets = ['panel','step','count','breakdown','recipients','more','groups','contactQuery','contactResults','picked','selection','schedule','preview','testStatus','firstTime','sendSummary','error','launch','sender','replyTo','subject','review','retry','groupEmpty','addEmail','previewItem','previewSubject','previewPosition','previewNav','previewPrev','previewNext','editPreview','previewRetry','followupCount','followupTime','followupZone'];
  connect() {
    this.form = this.element.querySelector('form');
    this.revealInvalid = event => {
      const panel = event.target.closest('[data-step][data-marketing-campaign-wizard-target=panel]');
      if (panel) this.setPanel(Number(panel.dataset.step));
      for (let parent = event.target.parentElement; parent && parent !== this.form; parent = parent.parentElement) {
        if (parent.tagName === 'DETAILS') parent.open = true;
      }
    };
    this.form.addEventListener('invalid', this.revealInvalid, true);
    this.recovery = new DraftRecovery(this.element, this.form, null, {
      excluded: ['contact_id', 'step_template_id', 'step_wait'],
      ignoreChange: event => event.target.type === 'search',
      capture: () => ({
        contacts: [...this.pickedTarget.children].map(chip => ({id: chip.querySelector('input').value, name: chip.dataset.name})),
        steps: [...this.form.querySelectorAll('[data-sequence-row]')].map(row => ({id: row.querySelector('[name=step_template_id]').value, wait: row.querySelector('[name=step_wait]').value})),
      }),
      restore: saved => {
        if (!saved) return;
        this.pickedTarget.replaceChildren();
        saved.contacts.forEach(row => this.addContact(row, false));
        for (const row of this.form.querySelectorAll('[data-sequence-row]')) {
          const kept = saved.steps.find(step => step.id === row.querySelector('[name=step_template_id]').value);
          if (!kept) row.remove();
          else row.querySelector('[name=step_wait]').value = kept.wait;
        }
        this.renumberSteps();
      },
    });
    this.restoreSubmission = () => {
      this.submitting = false;
      this.form.inert = false;
      this.form.removeAttribute('aria-busy');
      this.updateSummary();
    };
    window.addEventListener('pageshow', this.restoreSubmission);
    this.filterGroups();
    this.offset = 0;
    this.timingChanged();
    this.estimate();
    this.preview();
    if (this.element.dataset.initialPanel === '3') this.setPanel(3);
  }
  disconnect() {
    clearTimeout(this.timer); clearTimeout(this.searchTimer);
    this.estimateAbort?.abort(); this.searchAbort?.abort(); this.previewAbort?.abort();
    window.removeEventListener('pageshow', this.restoreSubmission);
    this.form.removeEventListener('invalid', this.revealInvalid, true);
    this.recovery.disconnect();
  }
  headers() {return {'Content-Type':'application/json',Accept:'application/json','X-CSRFToken':this.form.elements.csrf_token.value};}
  async request(url, data, signal) {
    const response = await fetch(url,{method:'POST',headers:this.headers(),body:JSON.stringify(data),signal});
    const json = await response.json();
    if (!response.ok || json.error) throw new Error(json.error || 'Could not load this. Please try again.');
    return json;
  }
  filter() {
    const data = new FormData(this.form);
    return {groups:data.getAll('groups'),owners:data.getAll('owners'),contact_ids:data.getAll('contact_id'),
      ...Object.fromEntries(['zips','cities','states'].map(k=>[k,String(data.get(k)||'').split(',').map(v=>v.trim()).filter(Boolean)])),
      whole_org:data.has('whole_org'),require_consent:data.has('require_consent')};
  }
  changed(event) {
    if (event?.target?.type === 'search') return;
    if (event?.target?.name === 'owners' && event.target.checked && this.form.elements.whole_org) this.form.elements.whole_org.checked = false;
    this.filterGroups();
    this.recovery.save();
    this.updateSummary();
    if (['scheduled_at','schedule_date','schedule_time','timezone','timing','send_hour','name','from_name','reply_to','step_wait'].includes(event?.target?.name)) return;
    this.estimateAbort?.abort();
    this.estimateGeneration = (this.estimateGeneration || 0) + 1;
    this.count = null;
    this.launchTarget.disabled = true;
    if (this.hasReviewTarget) this.reviewTarget.disabled = true;
    this.countTargets.forEach(el=>el.textContent='Checking recipients…');
    clearTimeout(this.timer);
    this.timer = setTimeout(()=>this.estimate(),250);
  }
  async estimate(append=false) {
    this.estimateAbort?.abort(); this.estimateAbort = new AbortController();
    const generation = this.estimateGeneration = (this.estimateGeneration || 0) + 1;
    if (!append) this.offset = 0;
    if (this.hasRetryTarget) this.retryTarget.hidden = true;
    this.launchTarget.disabled = true;
    try {
      const data = await this.request(this.estimateUrlValue,{...this.filter(),offset:this.offset},this.estimateAbort.signal);
      if (generation !== this.estimateGeneration) return;
      this.count = data.sendable;
      this.countTargets.forEach(el=>el.textContent=`${data.sendable.toLocaleString()} ${data.sendable === 1 ? 'recipient' : 'recipients'}`);
      const reasons = Object.entries(data.breakdown).map(([key,value])=>`${value} ${key.replaceAll('_',' ')}`);
      const breakdown = data.excluded ? `${data.excluded} excluded: ${reasons.join(', ')}.` : 'No exclusions in this selection.';
      this.breakdownTargets.forEach(el=>el.textContent=breakdown);
      for (const list of this.recipientsTargets) {
        if (!append) list.replaceChildren();
        for (const row of data.recipients) {
          const item=document.createElement('div'); item.className='mkt-recipient';
          const name=document.createElement('strong');name.textContent=row.name||row.email;
          const detail=document.createElement('small'); detail.textContent=`${row.email || 'No email'} · ${row.reason}`;
          item.append(name,detail);list.append(item);
        }
        if (!data.recipients.length && !append) list.textContent = 'Choose contacts or groups to see recipients here.';
      }
      this.moreTargets.forEach(el=>el.hidden = !data.has_more);
      this.estimateError=''; this.updateSummary();
    } catch(error) {
      if (error.name === 'AbortError' || generation !== this.estimateGeneration) return;
      this.count=null;
      this.countTargets.forEach(el=>el.textContent='Recipient check unavailable');
      this.breakdownTargets.forEach(el=>el.textContent='Could not check recipients. Please try again.');
      if (this.hasRetryTarget) this.retryTarget.hidden = false;
      this.estimateError=error.message;this.updateSummary();
      this.recipientsTargets.forEach(el=>el.replaceChildren());this.moreTargets.forEach(el=>el.hidden=true);
    }
  }
  retryEstimate() {this.estimate();}
  moreRecipients() {this.offset+=50;this.estimate(true);}
  scopeChanged(event) {
    if (event.currentTarget.checked) this.form.querySelectorAll('[name="owners"]').forEach(el=>el.checked=false);
    else this.groupsTarget.querySelectorAll('[data-own="false"] input').forEach(el=>el.checked=false);
    this.filterGroups();this.changed();
  }
  filterGroups(event) {
    if (event) this.groupQuery=event.currentTarget.value.toLowerCase();
    const org=this.form.elements.whole_org?.checked;
    for (const row of this.groupsTarget.querySelectorAll('label')) {
      row.hidden=(!org && row.dataset.own === 'false' && !row.querySelector('input').checked) || !row.textContent.toLowerCase().includes(this.groupQuery||'');
    }
    if (this.hasGroupEmptyTarget) {
      this.groupEmptyTarget.hidden = !this.groupQuery || [...this.groupsTarget.querySelectorAll('label')].some(row => !row.hidden);
    }
  }
  searchContacts() {
    clearTimeout(this.searchTimer);this.searchAbort?.abort();
    const q=this.contactQueryTarget.value.trim();
    if (!q) {this.contactResultsTarget.hidden=true;return;}
    this.searchTimer=setTimeout(async()=>{
      this.searchAbort=new AbortController();
      try {
        const response=await fetch(`${this.contactsUrlValue}?q=${encodeURIComponent(q)}`,{signal:this.searchAbort.signal,headers:{Accept:'application/json'}});
        if (!response.ok) throw new Error('Contact search is unavailable. Try again.');
        const rows=await response.json();this.contactResultsTarget.replaceChildren();
        for(const row of rows){const li=document.createElement('li');const button=document.createElement('button');button.type='button';button.textContent=`${row.name} · ${row.email||'No email'}`;button.addEventListener('click',()=>this.addContact(row));li.append(button);this.contactResultsTarget.append(li);}
        if(!rows.length) this.contactResultsTarget.textContent='No matching contacts.';
        this.contactResultsTarget.hidden=false;
      }catch(error){if(error.name!=='AbortError'){this.contactResultsTarget.textContent=error.message;this.contactResultsTarget.hidden=false;}}
    },200);
  }
  addContact(row, notify=true){
    if([...this.pickedTarget.querySelectorAll('input')].some(el=>el.value===String(row.id))) return;
    const chip=document.createElement('span');chip.className='mkt-picked__chip';chip.dataset.name=row.name;chip.append(document.createTextNode(row.name));
    const input=document.createElement('input');input.type='hidden';input.name='contact_id';input.value=row.id;
    const button=document.createElement('button');button.type='button';button.textContent='×';button.setAttribute('aria-label',`Remove ${row.name}`);button.addEventListener('click',()=>{chip.remove();this.changed();});chip.append(input,button);this.pickedTarget.append(chip);
    this.contactQueryTarget.value='';this.contactResultsTarget.hidden=true;if(notify)this.changed();
  }
  removeContact(event){event.currentTarget.closest('.mkt-picked__chip').remove();this.changed();}
  removeStep(event){
    const row=event.currentTarget.closest('[data-sequence-row]');
    const step=Number(row.querySelector('.mkt-sequence-pick')?.dataset.step);
    const current=this.currentStep||0;
    if (step<=current&&current>0) this.currentStep=current-1;
    row.remove();this.renumberSteps();this.changed();
  }
  renumberSteps(){
    if (this.hasAddEmailTarget) this.addEmailTarget.disabled = this.form.querySelectorAll('[data-sequence-row]').length >= 9;
    [...this.form.querySelectorAll('[data-sequence-row]')].forEach((row,index)=>{
      row.querySelector('[name=action]').value=`edit_email:${index+1}`;
      row.querySelector('[name=action]').setAttribute?.('aria-label', `Edit email ${index+2}`);
      row.querySelector('.mkt-sequence-number').textContent=index+2;
      const pick=row.querySelector('.mkt-sequence-pick');
      if (pick) pick.dataset.step=index+1;
      row.querySelector('[data-action*=removeStep]')?.setAttribute('aria-label', `Remove follow-up ${index+1}`);
    });
    if (this.hasPreviewItemTarget) this.preview();
  }
  showStep(event){
    this.setPanel(Number(event.currentTarget.dataset.step));
  }
  setPanel(n){
    this.form.elements.panel.value = n === 3 ? 'review' : 'recipients';
    this.panelTargets.forEach(el=>el.hidden=Number(el.dataset.step)!==n);
    this.stepTargets.forEach(el=>el.setAttribute('aria-current',Number(el.dataset.step)===n?'step':'false'));
    this.panelTargets.find(el=>Number(el.dataset.step)===n)?.querySelector('h1')?.focus();
    this.updateSummary();
  }
  timingChanged(){
    const later=this.form.elements.timing.value==='later';this.scheduleTarget.hidden=!later;
    this.form.elements.scheduled_at.disabled=!later;
    this.form.elements.schedule_date.disabled=!later;this.form.elements.schedule_date.required=later;
    this.form.elements.schedule_time.disabled=!later;
    this.updateSummary();
  }
  scheduleChanged(){
    const f=this.form.elements;
    f.scheduled_at.value=f.schedule_date.value ? `${f.schedule_date.value}T${f.schedule_time.value}` : '';
    this.updateSummary();
  }
  scheduleLabel(value, zone){
    const [date,time]=value.split('T');
    const [year,month,day]=date.split('-').map(Number);
    const [hour,minute]=time.split(':').map(Number);
    const display=new Date(year,month-1,day,hour,minute).toLocaleString('en-US',{month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit',hour12:true});
    return `${display} · ${zone}`;
  }
  updateSummary(){
    const f=this.form.elements;
    const groups=[...this.form.querySelectorAll('[name="groups"]:checked')].map(el=>el.closest('label').textContent.trim());
    const people=this.form.querySelectorAll('[name="contact_id"]').length;
    const parts=[];
    if(f.whole_org?.checked) parts.push('Brokerage contacts');
    else parts.push(...[...this.form.querySelectorAll('[name="owners"]:checked')].map(el=>el.closest('label').textContent.trim()));
    parts.push(...groups);if(people)parts.push(`${people} added ${people===1?'person':'people'}`);
    for(const key of ['cities','states','zips'])if(f[key]?.value)parts.push(f[key].value);
    const selection=parts.join(' · ')||'No recipients selected';this.selectionTargets.forEach(el=>el.textContent=selection);
    const later=f.timing.value==='later';const zone=f.timezone.selectedOptions[0].textContent;
    const at=later&&f.scheduled_at.value ? this.scheduleLabel(f.scheduled_at.value,zone) : later?'Choose a date and time':'Send now';
    this.firstTimeTarget.textContent=at;
    const extra=this.form.querySelectorAll('[name="step_template_id"]').length;
    if (this.hasFollowupZoneTarget) this.followupZoneTarget.textContent = zone;
    if (this.hasFollowupCountTarget) this.followupCountTarget.textContent = `${extra + 1} ${extra ? 'emails' : 'email'}`;
    this.sequenceTiming(at);
    if (this.hasFollowupTimeTarget) this.followupTimeTarget.hidden = !extra;
    this.sendSummaryTarget.textContent = `${extra + 1} ${extra ? 'emails' : 'email'} for ${this.count ?? '…'} ${this.count === 1 ? 'recipient' : 'recipients'}. ${later ? '' : 'First email sends within a few minutes.'}`;
    this.launchTarget.textContent = this.submitting ? 'Saving campaign…' : later ? 'Schedule campaign' : extra ? 'Start campaign' : `Send to ${this.count??'…'} ${this.count===1?'recipient':'recipients'}`;
    this.senderTarget.textContent=f.from_name.value.trim()||this.senderTarget.dataset.default;
    this.replyToTarget.textContent=f.reply_to.value.trim()||this.replyToTarget.dataset.default;
    this.errorTarget.textContent=this.estimateError||this.previewError||(this.launchTarget.dataset.ready!=='true' ? '' : this.count===0 ? 'Choose at least one recipient with an eligible email address.' : later&&!f.scheduled_at.value ? 'Choose a date and time to schedule this campaign.' : '');
    if (this.hasReviewTarget) this.reviewTarget.disabled = !this.count;
    this.launchTarget.disabled=this.submitting||this.launchTarget.dataset.ready!=='true'||!this.count||!this.previewReady||(later&&!f.scheduled_at.value);
  }
  sequenceTiming(firstLabel){
    const first=this.form.querySelector?.('[data-sequence-first] [data-sequence-when]');
    if (first) first.textContent=firstLabel;
    let total=0;
    [...(this.form.querySelectorAll?.('[data-sequence-row]')||[])].forEach(row=>{
      const wait=Number(row.querySelector('[name=step_wait]')?.value)||0;total+=wait;
      const when=row.querySelector('[data-sequence-when]');
      if (when) when.textContent=`${total} ${total===1?'day':'days'} after the first email`;
    });
  }
  previewItems(){return this.hasPreviewItemTarget ? this.previewItemTargets : [];}
  currentTemplateId(){
    const item=this.previewItems()[this.currentStep||0];
    return item ? item.dataset.templateId : this.form.elements.template_id.value;
  }
  showPreviewPosition(){
    const items=this.previewItems();
    const step=this.currentStep||0;
    items.forEach((item,index)=>item.setAttribute('aria-current',index===step?'true':'false'));
    if (this.hasEditPreviewTarget) this.editPreviewTarget.value=`edit_email:${step}`;
    if (this.hasPreviewPositionTarget) this.previewPositionTarget.textContent=items.length>1 ? `Email ${step+1} of ${items.length}` : 'Email preview';
    if (this.hasPreviewNavTarget) this.previewNavTarget.hidden=items.length<2;
    if (this.hasPreviewPrevTarget) this.previewPrevTarget.disabled=step<=0;
    if (this.hasPreviewNextTarget) this.previewNextTarget.disabled=step>=items.length-1;
  }
  selectPreview(event){
    this.currentStep=Number(event.currentTarget.dataset.step)||0;
    this.preview();
    if (window.matchMedia?.('(max-width: 767px)').matches) this.previewTarget.closest('aside')?.scrollIntoView({behavior:'smooth',block:'start'});
  }
  previousPreview(){this.currentStep=Math.max(0,(this.currentStep||0)-1);this.preview();}
  nextPreview(){this.currentStep=Math.min(this.previewItems().length-1,(this.currentStep||0)+1);this.preview();}
  async preview(){
    if (this.hasPreviewRetryTarget) this.previewRetryTarget.hidden = true;
    const items=this.previewItems();
    if (items.length) this.currentStep=Math.min(Math.max(this.currentStep||0,0),items.length-1);
    const id=this.currentTemplateId();if(!id)return;
    this.showPreviewPosition();
    this.previewAbort?.abort();this.previewAbort=new AbortController();
    this.previewReady=false;this.previewError='';this.updateSummary();
    this.previewTarget.srcdoc = '';
    if (this.hasPreviewSubjectTarget) this.previewSubjectTarget.textContent = 'Loading preview…';
    try{
      const data=await this.request(this.previewAsUrlValue,{template_id:id},this.previewAbort.signal);
      this.previewTarget.srcdoc=data.html;
      if (!(this.currentStep||0)) this.subjectTarget.textContent=data.subject;
      if (this.hasPreviewSubjectTarget) this.previewSubjectTarget.textContent = data.subject;
      this.previewReady=true;
    }catch(error){
      if(error.name!=='AbortError') {
        this.previewError='Could not load the email preview. Please try again.';
        if (this.hasPreviewRetryTarget) this.previewRetryTarget.hidden = false;
        if (this.hasPreviewSubjectTarget) this.previewSubjectTarget.textContent = this.previewError;
      }
    }
    this.updateSummary();
  }
  async sendTest(event){
    const button=event.currentTarget;button.disabled=true;this.testStatusTarget.textContent='Sending your test…';
    try{const data=await this.request(this.testUrlValue,{template_id:this.currentTemplateId(),from_name:this.form.elements.from_name.value,reply_to:this.form.elements.reply_to.value});this.testStatusTarget.textContent=`Test sent to ${data.sent.join(', ')}.`;}
    catch(error){this.testStatusTarget.textContent=error.message;}
    finally{button.disabled=false;}
  }
  submit(event){
    if (this.submitting) {event.preventDefault(); return;}
    if(event.submitter?.value==='launch'&&this.launchTarget.disabled){event.preventDefault();return;}
    if(!event.defaultPrevented) {
      this.recovery.submitting();
      this.submitting = true;
      this.form.inert = true;
      this.form.setAttribute('aria-busy', 'true');
      // Keep the submitter enabled so its action reaches the native POST.
      if (event.submitter?.value === 'launch') event.submitter.textContent = 'Starting campaign…';
    }
  }
}
