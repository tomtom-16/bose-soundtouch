// Exercise the actual embedded UI with a minimal DOM; no browser dependency.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const script=execFileSync('python3',['-c','import bose_radio; print(bose_radio.CONTROL_PAGE.split("<script>")[1].split("</script>")[0])'],{encoding:'utf8'});
const elements=new Map();
class Element{
  constructor(){this.children=[];this.options=[];this.style={};this.parts={};this.value='';this.textContent='';this.classList={add(){},remove(){},toggle(){}};}
  setAttribute(name,value){(this.attributes??={})[name]=value}
  set id(id){this._id=id;elements.set(id,this)}
  get id(){return this._id}
  get childElementCount(){return this.children.length}
  get selectedOptions(){return this.options.filter(o=>o.value===this.value)}
  add(o){this.options.push(o);if(this.options.length===1)this.value=o.value}
  appendChild(c){this.children.push(c)}
  replaceChildren(){this.children=[]}
  querySelector(s){return this.parts[s]??=new Element()}
}
const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
const requests=[];
let fail=false;
const context=vm.createContext({document:{getElementById:get,createElement:()=>new Element()},
  Option:function(textContent,value){Object.assign(this,{textContent,value})},
  fetch:async p=>{requests.push(p);return {ok:!fail,json:async()=>({ok:!fail,error:'test failure'})}},
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
  assert.equal(get('stop').hidden,false);
  assert.equal(get('hTitle').textContent,'One');
  get('target').children[1].onclick();
  assert.equal(get('target').children[0].attributes['aria-pressed'],'false');
  assert.equal(get('target').children[1].attributes['aria-pressed'],'true');
  assert.equal(get('grid').children[0].id,'st-two');
  assert.equal(get('hTitle').textContent,'Two');
  assert.equal(get('vol').value,30);
  await get('grid').children[0].onclick();
  assert.ok(requests.includes('/api/play/two?speaker=192.0.2.2&volume=30'));
  await get('stop').onclick();
  assert.ok(requests.includes('/api/stop?speaker=192.0.2.2'));
  status.speakers[1].playStatus='STANDBY';render();
  assert.equal(get('stop').hidden,true);
  get('targetAll').onclick();
  assert.equal(get('targetAll').attributes['aria-pressed'],'true');
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
  console.log('Web UI: preset order, overrides, target requests, source display, stop visibility, segmented selection and failure recovery passed');
})().catch(e=>{console.error(e);process.exitCode=1});
