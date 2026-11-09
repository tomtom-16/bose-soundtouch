// Exercise the actual web/app.js with a minimal DOM; no browser dependency.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');
const path=require('node:path');
const script=readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const elements=new Map();
let focusedElement=null;
class Element{
  constructor(){this.children=[];this.options=[];this.style={setProperty(name,value){this[name]=value}};this.parts={};this.value='';this.textContent='';this.classList={add(){},remove(){},toggle(){},contains(){return false}};}
  setAttribute(name,value){(this.attributes??={})[name]=value}
  set id(id){this._id=id;elements.set(id,this)}
  get id(){return this._id}
  get childElementCount(){return this.children.length}
  get selectedOptions(){return this.options.filter(o=>o.value===this.value)}
  add(o){this.options.push(o);if(this.options.length===1)this.value=o.value}
  appendChild(c){this.children.push(c);c.parent=this}
  remove(){this.parent.children=this.parent.children.filter(c=>c!==this)}
  focus(){focusedElement=this}
  getAnimations(){return []}
  hidePopover(){this.open=false}
  showModal(){this.open=true}
  close(){this.open=false}
  replaceChildren(){this.children=[];this.options=[];this.value=''}
  querySelector(s){
    const named=s.match(/^\[name="(.+)"\]$/);
    if(named)return this.children.flatMap(c=>c.children).find(c=>c.name===named[1]);
    return this.parts[s]??=new Element();
  }
}
const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
const requests=[];
const writes=[];
let speakerConfig={Living:{ip:'192.0.2.1',deviceID:'AABBCCDDEEFF'}};
let fail=false;
let discoverySpeakers=[],waitDiscovery=null;
const discovered={name:'Bureau <Bose>',ip:'192.0.2.8',deviceID:'123456ABCDEF',model:'SoundTouch 10'};
const context=vm.createContext({document:{getElementById:get,createElement:()=>new Element()},
  Option:function(textContent,value){Object.assign(this,{textContent,value})},
  fetch:async (p,options)=>{requests.push(p);if(options)writes.push(options);
    if((p==='/api/speakers/discover'||p==='/api/speakers/identify')&&waitDiscovery)await waitDiscovery;
    if(p==='/api/speakers'&&options&&!fail){
      speakerConfig=Object.fromEntries(JSON.parse(options.body).speakers.map(s=>[s.name,
        {ip:s.ip,deviceID:s.deviceID.toUpperCase(),model:s.model}]));
    }
    return {ok:!fail,json:async()=>fail?{ok:false,error:'test failure'}:
      p==='/api/stations'?{stations:{one:{note:'One',url:'https://example.com/live'}},revision:'revision-1',usages:{one:{direct:[{scope:'default',numbers:['1']}],effective:[{scope:'Living',numbers:['1']}]}}}:
      p==='/api/speakers/discover'?{ok:true,speakers:discoverySpeakers}:
      p==='/api/speakers/identify'?{ok:true,speaker:discovered}:
      p==='/api/speakers'?{ok:true,speakers:speakerConfig,revision:'speaker-revision'}:
      p==='/api/presets'?JSON.parse(JSON.stringify({presets:status.presets,stations:status.stations,
        speakers:Object.fromEntries(status.speakers.map(s=>[s.name,{ip:s.ip}]))})):
      p==='/api/status'?status:{ok:true}}},
  setTimeout:()=>0,clearTimeout(){},setInterval(){}});
vm.runInContext(script.replace('refresh();setInterval(refresh,5000);',''),context);
const stations=['four','five','one','two','three','six','extra'].map(id=>({id,note:id}));
const status={stations,presets:{default:{1:'one',2:'two',3:'three',4:'four',5:'five',6:'six'},Bath:{1:'two',2:'one',3:'three',4:'four',5:'five',6:'six'},Living:{1:'one',2:'two',3:'three',4:'four',5:'five',6:'six'}},
  default_speaker:'Living',speakers:[{name:'Living',ip:'192.0.2.1',volume:20,station:'one',playStatus:'PLAY_STATE'},
    {name:'Bath',ip:'192.0.2.2',volume:30,station:'two',playStatus:'PLAY_STATE'}]};
context.status=status;
function render(){vm.runInContext('render(status)',context)}
(async()=>{
  render();
  assert.deepEqual(get('grid').children.map(c=>c.id),['preset-1','preset-2','preset-3','preset-4','preset-5','preset-6']);
  assert.deepEqual(get('grid').children.map(c=>c.stationId),['one','two','three','four','five','six']);
  assert.equal(get('grid').children[0].querySelector('.preset').textContent,'Preset 1');
  assert.equal(get('target').children.length,2);
  assert.equal(get('target').children[0].attributes['aria-pressed'],'true');
  assert.equal(get('spkList').hidden,true);
  assert.ok(!script.includes('spkToggle'));
  assert.equal(get('stop').hidden,false);
  assert.equal(get('hTitle').textContent,'one');
  get('target').children[1].onclick();
  assert.equal(get('target').children[0].attributes['aria-pressed'],'false');
  assert.equal(get('target').children[1].attributes['aria-pressed'],'true');
  assert.equal(get('grid').children[0].stationId,'two');
  assert.equal(get('hTitle').textContent,'two');
  assert.equal(get('vol').value,30);
  await get('grid').children[0].onclick();
  assert.ok(requests.includes('/api/play/two?speaker=192.0.2.2&volume=30'));
  await get('stop').onclick();
  assert.ok(requests.includes('/api/stop?speaker=192.0.2.2'));
  status.speakers[1].playStatus='STANDBY';render();
  assert.equal(get('stop').hidden,true);
  get('targetAll').onclick();
  assert.equal(get('targetAll').attributes['aria-pressed'],'true');
  assert.equal(get('spkList').hidden,false);
  assert.deepEqual(get('spkList').children.map(c=>c.querySelector('input').value),[20,30]);
  get('target').children[1].onclick();
  assert.equal(get('spkList').hidden,true);
  get('targetAll').onclick();
  assert.equal(get('spkList').hidden,false);
  assert.equal(get('stop').hidden,false);
  status.speakers[0].playStatus='STANDBY';render();
  assert.equal(get('stop').hidden,true);
  status.speakers.forEach(s=>s.playStatus='PLAY_STATE');render();
  assert.equal(get('hTitle').textContent,'Plusieurs sources');
  await get('grid').children[0].onclick();
  assert.ok(requests.some(p=>p.startsWith('/api/play/one?speaker=all&volume=')));
  fail=true;
  await get('grid').children[0].onclick();
  assert.ok(get('target').children.every(b=>b.disabled===false));
  assert.equal(get('targetAll').disabled,false);
  assert.ok(get('toast').textContent.includes('test failure'));
  fail=false;
  await get('speakersOpen').onclick();
  assert.equal(get('settings').open,true);
  const row=get('speakerEditors').children[0];
  assert.equal(row.children[0].textContent,'Living');
  assert.equal(row.children.length,3); // name and actions, no inline inputs
  row.children[1].onclick();
  assert.equal(get('speakerEdit').open,true);
  assert.equal(get('speakerDeviceID').value,'AABBCCDDEEFF');
  get('speakerName').value='Salon <test>';
  const beforeSpeakerEdit=writes.length;
  await get('speakerForm').onsubmit({preventDefault(){}});
  assert.equal(get('speakerEdit').open,false);
  assert.equal(writes.length,beforeSpeakerEdit+1);
  assert.equal(writes.at(-1).method,'POST');
  assert.deepEqual(JSON.parse(writes.at(-1).body).speakers,[{
    original_name:'Living',name:'Salon <test>',ip:'192.0.2.1',deviceID:'AABBCCDDEEFF',model:''}]);
  assert.equal(get('settings').open,true);
  assert.equal(get('speakerEditors').children[0].children[0].textContent,'Salon <test>');
  // The next edit uses the saved name, including after a rename.
  get('speakerEditors').children[0].children[1].onclick();
  get('speakerName').value='Salon';
  await get('speakerForm').onsubmit({preventDefault(){}});
  assert.equal(JSON.parse(writes.at(-1).body).speakers[0].original_name,'Salon <test>');
  get('speakerAdd').onclick();
  assert.equal(get('speakerName').value,'');
  get('speakerName').value='Bureau 2';get('speakerIp').value='192.0.2.2';
  get('speakerDeviceID').value='112233445566';
  const beforeSpeakerCancel=writes.length;
  await get('speakerCancel').onclick();
  assert.equal(writes.length,beforeSpeakerCancel);
  assert.equal(get('speakerEditors').children.length,1);
  get('speakerAdd').onclick();
  get('speakerName').value='Bureau 2';get('speakerIp').value='192.0.2.2';
  get('speakerDeviceID').value='112233445566';
  fail=true;
  await get('speakerForm').onsubmit({preventDefault(){}});
  assert.equal(get('speakerEdit').open,true);
  assert.equal(get('speakerEditError').textContent,'test failure');
  assert.equal(get('speakerFields').disabled,false);
  assert.equal(get('speakerName').value,'Bureau 2');
  assert.equal(get('speakerEditors').children.length,1); // failed write leaves the list intact
  fail=false;
  await get('speakerForm').onsubmit({preventDefault(){}});
  assert.equal(JSON.parse(writes.at(-1).body).speakers[0].original_name,'Salon');
  assert.equal(JSON.parse(writes.at(-1).body).speakers[1].original_name,'');
  assert.deepEqual(get('speakerEditors').children.map(r=>r.children[0].textContent),['Bureau 2','Salon']);
  get('speakerSearch').value='BÚREAU';get('speakerSearch').oninput();
  assert.equal(get('speakerEditors').children.length,1);
  get('speakerSearch').value='absent';get('speakerSearch').oninput();
  assert.equal(get('speakerEditors').children.length,0);
  get('speakerSearch').value='';get('speakerSearch').oninput();
  const beforeSpeakerDelete=writes.length;
  get('speakerEditors').children[0].children[2].onclick();
  assert.equal(get('speakerDelete').open,true);
  assert.ok(get('speakerDeleteText').textContent.includes('Bureau 2'));
  await get('speakerDeleteCancel').onclick();
  assert.equal(writes.length,beforeSpeakerDelete);
  assert.equal(get('speakerEditors').children.length,2);
  get('speakerEditors').children[0].children[2].onclick();
  fail=true;await get('speakerDeleteConfirm').onclick();
  assert.equal(get('speakerDelete').open,true);
  assert.equal(get('speakerDeleteError').textContent,'test failure');
  assert.equal(get('speakerDeleteConfirm').disabled,false);
  assert.equal(get('speakerEditors').children.length,2);
  fail=false;await get('speakerDeleteConfirm').onclick();
  assert.equal(get('speakerDelete').open,false);
  assert.equal(get('speakerEditors').children.length,1);
  assert.deepEqual(JSON.parse(writes.at(-1).body).speakers.map(s=>s.name),['Salon']);
  await get('settingsClose').onclick();
  await get('speakersOpen').onclick();
  assert.equal(get('speakerEditors').children[0].children[0].textContent,'Salon');
  await get('settingsClose').onclick();
  speakerConfig={};
  await get('speakersOpen').onclick();
  assert.ok(get('speakerEditors').children[0].textContent.includes('Aucune enceinte'));
  assert.equal(get('speakerEdit').open,false);
  await get('settingsClose').onclick();
  speakerConfig={Living:{ip:'192.0.2.1',deviceID:'AABBCCDDEEFF'}};
  assert.ok(!script.includes("$('settingsSave')"));
  await get('stationsOpen').onclick();
  assert.equal(get('stationsSettings').open,true);
  assert.equal(get('stationList').children.length,1);
  get('stationSearch').value='absent';get('stationSearch').oninput();
  assert.equal(get('stationList').children.length,0);
  get('stationSearch').value='ÔNE';get('stationSearch').oninput();
  assert.equal(get('stationList').children.length,1);
  get('stationList').children[0].children[1].onclick();
  assert.equal(get('stationName').value,'One');
  assert.equal(get('stationEdit').open,true);
  get('stationName').value='Renamed';
  await get('stationForm').onsubmit({preventDefault(){}});
  assert.deepEqual(JSON.parse(writes.at(-1).body),{action:'save',id:'one',name:'Renamed',url:'https://example.com/live',revision:'revision-1'});
  get('stationAdd').onclick();
  assert.equal(get('stationName').value,'');
  const beforeCancel=writes.length;
  await get('stationCancel').onclick();
  assert.equal(get('stationEdit').open,false);
  assert.equal(writes.length,beforeCancel);
  await get('stationList').children[0].children[2].onclick();
  assert.equal(get('stationDelete').open,true);
  assert.ok(get('stationDeleteText').textContent.includes('Living (preset(s) 1)'));
  await get('stationDeleteCancel').onclick();
  assert.equal(writes.length,beforeCancel);
  await get('stationList').children[0].children[2].onclick();
  await get('stationDeleteConfirm').onclick();
  assert.deepEqual(JSON.parse(writes.at(-1).body),{action:'delete',id:'one',confirmed:true,revision:'revision-1'});
  assert.equal(get('stationDelete').open,false);
  get('stationAdd').onclick();fail=true;
  await get('stationForm').onsubmit({preventDefault(){}});
  assert.equal(get('stationEditError').textContent,'test failure');
  assert.equal(get('stationFields').disabled,false);
  assert.equal(get('stationEdit').open,true);
  fail=false;await get('stationCancel').onclick();await get('stationsClose').onclick();
  get('target').children[1].onclick();
  await get('presetsOpen').onclick();
  assert.equal(get('presetsSettings').open,true);
  assert.equal(get('presetsScope').value,'Bath');
  const preset=n=>get('presetEditors').children[n-1];
  const choose=async(n,label)=>{
    preset(n).onclick();
    await get('presetChoices').children.find(button=>button.textContent===label).onclick();
  };
  assert.equal(get('presetEditors').children.length,6);
  assert.equal(preset(1).children[1].textContent,'two');
  assert.equal(preset(3).children[1].textContent,'three'); // independent speaker assignment
  preset(1).onclick();
  assert.equal(get('presetPicker').open,true);
  assert.deepEqual(get('presetChoices').children.slice(1).map(b=>b.textContent),['extra','five','four','one','six','three','two']);
  get('presetSearch').value='THRÉE';get('presetSearch').oninput();
  assert.deepEqual(get('presetChoices').children.map(b=>b.textContent),['Sans affectation','three']);
  const beforePickerCancel=writes.length;
  await get('presetPickerClose').onclick();
  assert.equal(writes.length,beforePickerCancel);
  assert.equal(focusedElement,preset(1));
  preset(3).onclick();
  await get('presetPicker').oncancel({preventDefault(){}});
  assert.equal(focusedElement,preset(3));
  await choose(1,'Sans affectation');
  assert.deepEqual(JSON.parse(writes.at(-1).body),{presets:{Bath:{1:null,2:'one',3:'three',4:'four',5:'five',6:'six'}}});
  assert.equal(get('presetPicker').open,false);
  assert.equal(preset(1).children[1].textContent,'Sans affectation');
  assert.equal(focusedElement,preset(1));
  fail=true;
  await choose(3,'extra');
  assert.equal(get('presetPicker').open,true);
  assert.equal(get('presetPickerError').textContent,'test failure');
  assert.equal(preset(3).children[1].textContent,'three');
  assert.ok(get('presetChoices').children.every(b=>!b.disabled));
  fail=false;
  await get('presetChoices').children.find(b=>b.textContent==='extra').onclick();
  assert.deepEqual(JSON.parse(writes.at(-1).body),{presets:{Bath:{1:null,2:'one',3:'extra',4:'four',5:'five',6:'six'}}});
  assert.equal(preset(3).children[1].textContent,'extra');
  assert.equal(get('presetPicker').open,false);
  // Switching speakers does not leak an edit to the global list or another speaker.
  get('presetsScope').value='Living';get('presetsScope').onchange();
  await choose(2,'six');
  assert.deepEqual(JSON.parse(writes.at(-1).body),{presets:{Living:{1:'one',2:'six',3:'three',4:'four',5:'five',6:'six'}}});
  get('presetsScope').value='default';get('presetsScope').onchange();
  await choose(3,'five');
  assert.deepEqual(JSON.parse(writes.at(-1).body),{presets:{default:{1:'one',2:'two',3:'five',4:'four',5:'five',6:'six'}}});
  await choose(6,'Sans affectation');
  assert.deepEqual(JSON.parse(writes.at(-1).body),{presets:{default:{1:'one',2:'two',3:'five',4:'four',5:'five'}}});
  assert.equal(preset(6).children[1].textContent,'Sans affectation');
  get('presetsScope').value='Living';get('presetsScope').onchange();
  assert.equal(preset(2).children[1].textContent,'six'); // explicit override preserved
  assert.equal(preset(3).children[1].textContent,'three'); // global change has no effect
  assert.equal(preset(6).children[1].textContent,'six');
  get('presetsScope').value='Bath';get('presetsScope').onchange();
  assert.equal(preset(1).children[1].textContent,'Sans affectation'); // explicit null preserved
  await get('presetsClose').onclick();
  get('targetAll').onclick();
  await get('presetsOpen').onclick();
  assert.equal(get('presetsScope').value,'default');
  await get('presetsClose').onclick();
  get('target').children[1].onclick();
  status.presets.Bath[1]=null;render();
  assert.equal(get('grid').children[0].querySelector('.preset').textContent,'Preset 1');
  assert.equal(get('grid').children[0].querySelector('.nm').textContent,'Sans affectation');
  assert.equal(get('grid').children[0].disabled,true);
  const beforeUnassigned=writes.length,requestsBeforeUnassigned=requests.length;
  await get('grid').children[0].onclick();
  assert.equal(requests.length,requestsBeforeUnassigned);
  assert.equal(writes.length,beforeUnassigned);
  status.speakers=[{name:'New <name>',ip:'192.0.2.3',volume:12,playStatus:'STANDBY'}];render();
  assert.equal(get('grid').children.length,6);
  assert.deepEqual(get('grid').children.map(c=>c.querySelector('.preset').textContent),
    ['Preset 1','Preset 2','Preset 3','Preset 4','Preset 5','Preset 6']);
  assert.ok(get('grid').children.every(c=>c.disabled&&c.querySelector('.nm').textContent==='Sans affectation'));
  status.presets['New <name>']={1:'one',2:'one',3:'unknown',4:null};render();
  assert.deepEqual(get('grid').children.slice(0,2).map(c=>c.querySelector('.nm').textContent),['one','one']);
  assert.ok(get('grid').children.slice(2).every(c=>c.disabled&&c.querySelector('.nm').textContent==='Sans affectation'));
  get('targetAll').onclick();
  assert.equal(get('grid').children[0].querySelector('.nm').textContent,'one');
  get('target').children[0].onclick();
  assert.equal(get('target').children.length,1);
  assert.equal(get('target').children[0].textContent,'New <name>');
  assert.equal(get('spkList').children.length,1);
  assert.equal(get('spkList').children[0].querySelector('.nm').textContent,'New <name>');
  // Volume fill follows polling, +/- buttons and direct slider input.
  assert.equal(get('vol').style['--level'],(12/70*100)+'%');
  get('vup').onclick();
  assert.equal(get('vol').value,17);
  assert.equal(get('vol').style['--level'],(17/70*100)+'%');
  get('vdn').onclick();
  assert.equal(get('vol').value,12);
  get('vol').value=35;get('vol').oninput();
  assert.equal(get('vol').style['--level'],'50%');
  get('vol').value=70;get('vup').onclick();
  assert.equal(get('vol').value,70);
  assert.equal(get('vol').style['--level'],'100%');
  get('vol').value=0;get('vdn').onclick();
  assert.equal(get('vol').value,0);
  assert.equal(get('vol').style['--level'],'0%');
  const speakerSlider=get('spkList').children[0].querySelector('input');
  assert.equal(speakerSlider.attributes['aria-label'],'Volume : New <name>');
  speakerSlider.value=35;speakerSlider.oninput();
  assert.equal(speakerSlider.style['--level'],'50%');
  status.speakers=[];render();
  assert.equal(get('target').children.length,0);
  assert.equal(get('spkList').children.length,0);
  get('targetAll').onclick();
  await get('presetsOpen').onclick();
  assert.equal(get('presetsScope').value,'default');
  assert.equal(get('presetEditors').children.length,6);
  assert.equal(get('presetsScope').options.length,1);
  assert.ok(!script.includes('Les presets seront configurables dans une prochaine étape'));
  // A long catalogue stays sorted, searchable, and opens a separate editor.
  vm.runInContext("stationConfig={stations:Object.fromEntries(Array.from({length:50},(_,i)=>['id-'+(50-i),{note:'Radio '+(50-i),url:'https://example.com/live'}]))};renderStationList()",context);
  get('stationSearch').value='';get('stationSearch').oninput();
  assert.equal(get('stationList').children.length,50);
  assert.equal(get('stationList').children[0].children[0].textContent,'Radio 1');
  assert.equal(get('stationList').children.at(-1).children[0].textContent,'Radio 50');
  get('stationList').children.at(-1).children[1].onclick();
  assert.equal(get('stationEdit').open,true);
  assert.equal(get('stationName').value,'Radio 50');
  await get('stationCancel').onclick();
  get('stationSearch').value='Radio 50';get('stationSearch').oninput();
  assert.equal(get('stationList').children.length,1);
  // A native close must wait for the exit animation, even without CSS overlay support.
  const dialog=get('settings');dialog.showModal();
  let finishAnimation;
  dialog.getAnimations=()=>[{finished:new Promise(resolve=>{finishAnimation=resolve})}];
  const closing=vm.runInContext("closeDialog($('settings'))",context);
  assert.equal(dialog.open,true);
  finishAnimation();await closing;
  assert.equal(dialog.open,false);
  dialog.getAnimations=()=>[];
  // Discovery only fills the draft. Existing devices are recognized by ID.
  discoverySpeakers=[discovered,{...discovered,name:'Autre nom',deviceID:'AABBCCDDEEFF'}];
  speakerConfig={Salon:{ip:'192.0.2.1',deviceID:'aabbccddeeff'}};
  await get('speakersOpen').onclick();get('speakerAdd').onclick();
  const savesBeforeScan=requests.filter(p=>p==='/api/speakers').length;
  await get('speakerScan').onclick();
  assert.equal(get('speakerScanResults').children.length,2);
  assert.equal(get('speakerScanResults').children[1].disabled,true);
  assert.ok(get('speakerScanResults').children[1].textContent.includes('Déjà configurée (Salon)'));
  get('speakerScanResults').children[0].onclick();
  assert.equal(get('speakerName').value,'Bureau <Bose>');
  assert.equal(get('speakerDeviceID').value,'123456ABCDEF');
  assert.equal(requests.filter(p=>p==='/api/speakers').length,savesBeforeScan);
  await get('speakerForm').onsubmit({preventDefault(){}});
  assert.equal(JSON.parse(writes.at(-1).body).revision,'speaker-revision');
  assert.equal(get('speakerEdit').open,false);
  get('speakerAdd').onclick();discoverySpeakers=[];
  await get('speakerScan').onclick();
  assert.ok(get('speakerScanStatus').textContent.includes('Aucune enceinte'));
  fail=true;await get('speakerScan').onclick();
  assert.equal(get('speakerEditError').textContent,'test failure');
  assert.equal(get('speakerSave').disabled,false);fail=false;
  // IP lookup retains a name entered locally and writes nothing.
  speakerConfig={};await get('speakerCancel').onclick();
  await get('settingsClose').onclick();await get('speakersOpen').onclick();get('speakerAdd').onclick();
  get('speakerName').value='Mon bureau';get('speakerIp').value='192.0.2.8';
  await get('speakerLookup').onclick();
  assert.equal(get('speakerName').value,'Mon bureau');
  assert.equal(get('speakerModel').value,'SoundTouch 10');
  // A cancelled scan must not populate a later editor, and may not submit while pending.
  let finishDiscovery;
  waitDiscovery=new Promise(resolve=>{finishDiscovery=resolve});discoverySpeakers=[discovered];
  const scanning=get('speakerScan').onclick();
  const beforePendingSubmit=writes.length;
  await get('speakerForm').onsubmit({preventDefault(){}});
  assert.equal(writes.length,beforePendingSubmit);
  await get('speakerCancel').onclick();get('speakerAdd').onclick();
  finishDiscovery();await scanning;waitDiscovery=null;
  assert.equal(get('speakerName').value,'');
  assert.equal(get('speakerScanResults').children.length,0);
  assert.equal(get('speakerSave').disabled,false);
  waitDiscovery=new Promise(resolve=>{finishDiscovery=resolve});
  get('speakerIp').value='192.0.2.8';
  const lookup=get('speakerLookup').onclick();get('speakerIp').value='192.0.2.9';
  finishDiscovery();await lookup;waitDiscovery=null;
  assert.equal(get('speakerIp').value,'192.0.2.9');
  assert.equal(get('speakerDeviceID').value,'');
  assert.ok(get('speakerScanStatus').textContent.includes('Adresse modifiée'));
  console.log('Web UI: playback, station catalogue CRUD, deletion confirmation, independent global/speaker presets and speaker settings passed');
})().catch(e=>{console.error(e);process.exitCode=1});
