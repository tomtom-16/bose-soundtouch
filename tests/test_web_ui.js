// Exercise the actual embedded UI with a minimal DOM; no browser dependency.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const script=execFileSync('python3',['-c','import bose_radio; print(bose_radio.CONTROL_PAGE.split("<script>")[1].split("</script>")[0])'],{encoding:'utf8'});
const elements=new Map();
class Element{
  constructor(){this.children=[];this.options=[];this.style={};this.parts={};this.value='';this.textContent='';this.classList={add(){},remove(){},toggle(){},contains(){return false}};}
  setAttribute(name,value){(this.attributes??={})[name]=value}
  set id(id){this._id=id;elements.set(id,this)}
  get id(){return this._id}
  get childElementCount(){return this.children.length}
  get selectedOptions(){return this.options.filter(o=>o.value===this.value)}
  add(o){this.options.push(o);if(this.options.length===1)this.value=o.value}
  appendChild(c){this.children.push(c);c.parent=this}
  remove(){this.parent.children=this.parent.children.filter(c=>c!==this)}
  focus(){}
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
const context=vm.createContext({document:{getElementById:get,createElement:()=>new Element()},
  Option:function(textContent,value){Object.assign(this,{textContent,value})},
  fetch:async (p,options)=>{requests.push(p);if(options)writes.push(options);
    return {ok:!fail,json:async()=>fail?{ok:false,error:'test failure'}:
      p==='/api/stations'?{stations:{one:{note:'One',url:'https://example.com/live'}},revision:'revision-1',usages:{one:{direct:[{scope:'default',numbers:['1']}],effective:[{scope:'Living',numbers:['1']}]}}}:
      p==='/api/speakers'?{ok:true,speakers:speakerConfig}:
      p==='/api/presets'?JSON.parse(JSON.stringify({presets:status.presets,stations:status.stations,
        speakers:Object.fromEntries(status.speakers.map(s=>[s.name,{ip:s.ip}]))})):
      p==='/api/status'?status:{ok:true}}},
  setTimeout:()=>0,clearTimeout(){},setInterval(){}});
vm.runInContext(script.replace('refresh();setInterval(refresh,5000);',''),context);
const stations=['four','five','one','two','three','six','extra'].map(id=>({id,note:id}));
const status={stations,presets:{default:{1:'one',2:'two',3:'three',4:'four',5:'five',6:'six'},Bath:{1:'two',2:'one'}},
  default_speaker:'Living',speakers:[{name:'Living',ip:'192.0.2.1',volume:20,station:'one',playStatus:'PLAY_STATE'},
    {name:'Bath',ip:'192.0.2.2',volume:30,station:'two',playStatus:'PLAY_STATE'}]};
context.status=status;
function render(){vm.runInContext('render(status)',context)}
(async()=>{
  render();
  assert.deepEqual(get('grid').children.map(c=>c.id),['st-one','st-two','st-three','st-four','st-five','st-six','st-extra']);
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
  assert.equal(get('grid').children[0].id,'st-two');
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
  row.querySelector('[name="name"]').value='Salon <test>';
  get('speakerAdd').onclick();
  assert.equal(get('speakerEditors').children.length,2);
  get('speakerEditors').children[1].children.at(-1).onclick();
  assert.equal(get('speakerEditors').children.length,1);
  await get('settingsForm').onsubmit({preventDefault(){}});
  assert.equal(writes.at(-1).method,'POST');
  assert.deepEqual(JSON.parse(writes.at(-1).body).speakers,[{
    original_name:'Living',name:'Salon <test>',ip:'192.0.2.1',deviceID:'AABBCCDDEEFF',model:''}]);
  assert.equal(get('settings').open,false);
  await get('speakersOpen').onclick();
  fail=true;
  await get('settingsForm').onsubmit({preventDefault(){}});
  assert.equal(get('settings').open,true);
  assert.equal(get('settingsError').textContent,'test failure');
  assert.equal(get('settingsSave').disabled,false);
  assert.equal(get('speakerEditors').children[0].disabled,false);
  await get('settingsCancel').onclick();
  assert.equal(get('settings').open,false);
  fail=false;
  await get('stationsOpen').onclick();
  assert.equal(get('stationsSettings').open,true);
  assert.equal(get('stationList').children.length,1);
  get('stationList').children[0].children[1].onclick();
  assert.equal(get('stationName').value,'One');
  get('stationName').value='Renamed';
  await get('stationForm').onsubmit({preventDefault(){}});
  assert.deepEqual(JSON.parse(writes.at(-1).body),{action:'save',id:'one',name:'Renamed',url:'https://example.com/live',revision:'revision-1'});
  get('stationAdd').onclick();
  assert.equal(get('stationName').value,'');
  const beforeCancel=writes.length;
  get('stationCancel').onclick();
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
  assert.equal(get('stationsError').textContent,'test failure');
  assert.equal(get('stationFields').disabled,false);
  assert.equal(get('stationForm').hidden,false);
  fail=false;await get('stationsClose').onclick();
  get('target').children[1].onclick();
  await get('presetsOpen').onclick();
  assert.equal(get('presetsSettings').open,true);
  assert.equal(get('presetsScope').value,'Bath');
  const preset=n=>get('presetEditors').children[n-1].children[0];
  const choose=(n,value)=>{preset(n).value=value&&value!=='inherit'?'station:'+value:value;preset(n).onchange()};
  const scope=value=>{get('presetsScope').value=value;get('presetsScope').onchange()};
  assert.equal(get('presetEditors').children.length,6);
  assert.equal(preset(1).value,'station:two');
  assert.equal(preset(3).value,'inherit');
  choose(1,''); // suppress a global preset
  choose(2,'inherit'); // remove an individual override
  scope('default');
  choose(3,'extra');
  choose(6,''); // clear a global slot
  scope('Bath');
  assert.equal(preset(1).value,'');
  assert.equal(preset(2).value,'inherit');
  assert.ok(preset(3).options[0].textContent.includes('extra'));
  fail=true;
  await get('presetsForm').onsubmit({preventDefault(){}});
  assert.equal(get('presetsSettings').open,true);
  assert.equal(get('presetsError').textContent,'test failure');
  assert.equal(get('presetsFields').disabled,false);
  assert.equal(get('presetsSave').disabled,false);
  fail=false;
  await get('presetsForm').onsubmit({preventDefault(){}});
  assert.deepEqual(JSON.parse(writes.at(-1).body),{presets:{Bath:{1:null},
    default:{1:'one',2:'two',3:'extra',4:'four',5:'five'}}});
  assert.equal(requests.at(-2),'/api/presets');
  assert.equal(get('presetsSettings').open,false);
  await get('presetsOpen').onclick();
  get('presetsReset').onclick();
  assert.ok(get('presetEditors').children.every(label=>label.children[0].value==='inherit'));
  await get('presetsForm').onsubmit({preventDefault(){}});
  assert.deepEqual(JSON.parse(writes.at(-1).body),{presets:{Bath:{}}});
  await get('presetsOpen').onclick();
  choose(1,'extra');
  const count=writes.length;
  await get('presetsCancel').onclick();
  assert.equal(writes.length,count);
  await get('presetsOpen').onclick();
  assert.equal(preset(1).value,'station:two');
  await get('presetsClose').onclick();
  status.presets.Bath[1]=null;render();
  assert.equal(get('grid').children.find(c=>c.id==='st-two').querySelector('.preset').textContent,'Sans preset');
  status.speakers=[{name:'New <name>',ip:'192.0.2.3',volume:12,playStatus:'STANDBY'}];render();
  assert.equal(get('target').children.length,1);
  assert.equal(get('target').children[0].textContent,'New <name>');
  assert.equal(get('spkList').children.length,1);
  assert.equal(get('spkList').children[0].querySelector('.nm').textContent,'New <name>');
  status.speakers=[];render();
  assert.equal(get('target').children.length,0);
  assert.equal(get('spkList').children.length,0);
  get('targetAll').onclick();
  await get('presetsOpen').onclick();
  assert.equal(get('presetsScope').value,'default');
  assert.equal(get('presetsReset').hidden,true);
  assert.equal(get('presetsScope').options.length,1);
  assert.ok(!script.includes('Les presets seront configurables dans une prochaine étape'));
  // A native close must wait for the exit animation, even without CSS overlay support.
  const dialog=get('settings');dialog.showModal();
  let finishAnimation;
  dialog.getAnimations=()=>[{finished:new Promise(resolve=>{finishAnimation=resolve})}];
  const closing=vm.runInContext("closeDialog($('settings'))",context);
  assert.equal(dialog.open,true);
  finishAnimation();await closing;
  assert.equal(dialog.open,false);
  console.log('Web UI: playback, station catalogue CRUD, deletion confirmation, presets, inheritance and speaker settings passed');
})().catch(e=>{console.error(e);process.exitCode=1});
