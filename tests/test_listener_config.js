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
class FakeSocket{
  constructor(url){this.url=url;this.handlers={};sockets.push(this)}
  on(event,handler){this.handlers[event]=handler}
  terminate(){this.terminated=true;this.handlers.close()}
}
const context=vm.createContext({
  require:name=>name==='fs'?{readFileSync:p=>{
    const value=files[path.basename(p)];if(value===null)throw Error('bad JSON');
    return JSON.stringify(value);
  }}:name==='ws'?FakeSocket:require(name),
  __dirname:path.resolve(__dirname,'..'),process:{env:{}},console:{log(){}},
  setInterval:fn=>intervals.push(fn),
  setTimeout:fn=>{const timer={fn};timeouts.push(timer);return timer},
  clearTimeout:timer=>{if(timer)timer.cancelled=true}
});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../button_listener.js'),'utf8'),context);
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
console.log('Listener: reload, removal, rename/IP changes, invalid file and retry cleanup passed');
