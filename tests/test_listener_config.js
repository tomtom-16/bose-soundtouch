// Reload the actual listener against fake files and sockets; no Bose is contacted.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const files={
  'config.json':{sync:false},
  'speakers.json':{Living:{ip:'192.0.2.1'}}
};
const sockets=[],timeouts=[],intervals=[];
let controlOptions;
class FakeSocket{
  constructor(url){this.url=url;this.handlers={};sockets.push(this)}
  on(event,handler){this.handlers[event]=handler}
  terminate(){this.terminated=true;this.handlers.close()}
}
const context=vm.createContext({
  require:name=>name==='fs'?{readFileSync:p=>{
    const value=files[path.basename(p)];if(value===null)throw Error('bad JSON');
    return JSON.stringify(value);
  }}:name==='ws'?FakeSocket:name==='./listener_control'?{startControl(options){controlOptions=options}}:require(name),
  __dirname:path.resolve(__dirname,'..'),process:{env:{}},console:{log(){}},
  setInterval:fn=>intervals.push(fn),
  setTimeout:fn=>{const timer={fn};timeouts.push(timer);return timer},
  clearTimeout:timer=>{if(timer)timer.cancelled=true}
});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../button_listener.js'),'utf8'),context);
files['presets.json']={default:{1:'radio',2:'other'},Living:{1:null,3:'local'}};
assert.deepEqual(JSON.parse(JSON.stringify(vm.runInContext("presetMapFor('Living')",context))),
  {1:null,3:'local'});
files['presets.json'].default[2]='updated';
assert.equal(vm.runInContext("presetMapFor('Living')['2']",context),undefined);
files['presets.json'].Living={};
assert.equal(vm.runInContext("presetMapFor('Living')['1']",context),undefined);
assert.deepEqual(JSON.parse(JSON.stringify(vm.runInContext("presetMapFor('New')",context))),{});
assert.equal(sockets.length,1);
assert.equal(sockets[0].url,'ws://192.0.2.1:8080');
intervals[0]();assert.equal(sockets.length,1); // unchanged config keeps the socket
files['speakers.json']=null;intervals[0]();
assert.equal(sockets[0].terminated,undefined); // malformed file doesn't disconnect
files['speakers.json']={Salon:{ip:'192.0.2.2'},Bath:{ip:'192.0.2.3'}};
intervals[0]();
assert.equal(sockets[0].terminated,true);
assert.equal(sockets.length,3);
assert.equal(timeouts.length,0); // retired socket must not reconnect
sockets[1].handlers.close();
assert.equal(timeouts.length,1);
files['speakers.json']={};intervals[0]();
assert.equal(timeouts[0].cancelled,true);
timeouts[0].fn();assert.equal(sockets.length,3); // obsolete retry is harmless
files['speakers.json']={New:{ip:'192.0.2.4'}};intervals[0]();
assert.equal(sockets.length,4); // supports starting from an empty config
(async()=>{
  const {createControl}=require('../listener_control');
  const failed=createControl(controlOptions,async()=>false);
  const timersBefore=timeouts.length;
  await failed.checkAll('New');
  assert.equal(controlOptions.connections.get('New').paused,true);
  assert.equal(controlOptions.connections.get('New').ws,null);
  const afterFailure=sockets.length;
  intervals[0]();assert.equal(sockets.length,afterFailure); // reload passif ne relance pas
  sockets.at(-1).handlers.close(); // fermeture tardive de l'ancienne socket
  assert.equal(timeouts.length,timersBefore);
  const recovered=createControl(controlOptions,async ws=>!!ws);
  await recovered.checkAll('New');
  assert.equal(controlOptions.connections.get('New').state,'connected');
  assert.equal(sockets.length,afterFailure+1);
  sockets.at(-1).handlers.close();
  assert.equal(timeouts.length,timersBefore+1); // reprise automatique hors suspension
  console.log('Listener: reload, removal, stale sockets, failed check suspension and manual recovery passed');
})().catch(e=>{console.error(e);process.exitCode=1});
