// WebSockets simulés et HTTP sur boucle locale ; aucune Bose contactée.
const assert=require('node:assert/strict');
const {EventEmitter,once}=require('node:events');
const {probe,createControl,startControl}=require('../listener_control');
class Socket extends EventEmitter {
  constructor(reply=true,state=1){super();this.reply=reply;this.readyState=state;this.pings=0}
  ping(token){this.pings++;if(this.reply)queueMicrotask(()=>this.emit('pong',Buffer.from(token)))}
  terminate(){this.terminated=true;this.readyState=3;this.emit('close')}
}
(async()=>{
  const live=new Socket();
  assert.equal(await probe(live,20),true);
  assert.equal(live.listenerCount('pong'),0);
  const dead=new Socket(false);
  assert.equal(await probe(dead,10),false);
  assert.equal(dead.listenerCount('close'),0);
  const opening=new Socket(true,0),pending=probe(opening,30);
  opening.readyState=1;opening.emit('open');assert.equal(await pending,true);
  const wrong=new Socket(false);wrong.ping=()=>wrong.emit('pong',Buffer.from('wrong'));
  assert.equal(await probe(wrong,10),false);
  assert.equal(await probe(new Socket(false,3)),false);

  const entry={ip:'192.0.2.1',ws:dead,state:'connected'};
  const connections=new Map([['Salon',entry]]);
  let reconnects=0,reload=0;
  const options={connections,reloadSpeakers(){reload++},reconnect(name,e){
    reconnects++;e.ws=new Socket();
  }};
  const control=createControl(options,ws=>probe(ws,15));
  control.snapshot();assert.equal(dead.pings,1); // lecture passive
  await Promise.all([control.checkAll('Salon'),control.checkAll('Salon')]);
  assert.equal(reconnects,1);assert.equal(dead.terminated,true);
  assert.equal(entry.state,'connected');assert.equal(entry.paused,false);
  await control.checkAll();assert.equal(reconnects,1);
  assert.ok(reload>0);
  await assert.rejects(control.checkAll('Absent'),/inconnue/);

  entry.ws=new Socket(false);
  const offline=createControl({...options,reconnect(name,e){e.ws=new Socket(false)}},ws=>probe(ws,10));
  await offline.checkAll();
  assert.equal(entry.state,'disconnected');assert.equal(entry.paused,true);assert.equal(entry.ws,null);
  await control.checkAll(); // rallumage, tentative manuelle ultérieure
  assert.equal(entry.state,'connected');

  let release;
  const retired={ip:'192.0.2.2',ws:new Socket()};connections.set('Retired',retired);
  const delayed=createControl(options,()=>new Promise(resolve=>{release=resolve}));
  const checking=delayed.checkAll('Retired');await Promise.resolve();
  connections.delete('Retired');release(false);await checking;
  assert.equal(reconnects,2); // aucune recréation après retrait

  process.env.BOSE_BUTTONS_PORT='0';
  const server=startControl(options);await once(server,'listening');
  try{
    const base=`http://127.0.0.1:${server.address().port}`;
    assert.equal(server.address().address,'127.0.0.1');
    assert.equal((await (await fetch(base+'/status')).json()).speakers.Salon.state,'connected');
    assert.equal((await fetch(base+'/check',{method:'POST',headers:{Origin:'http://evil.example'}})).status,403);
    assert.equal((await fetch(base+'/check?name=Absent',{method:'POST'})).status,400);
    assert.equal((await fetch(base+'/check?name=Salon',{method:'POST'})).status,200);
    assert.equal((await fetch(base+'/missing')).status,404);
  }finally{await new Promise(resolve=>server.close(resolve));delete process.env.BOSE_BUTTONS_PORT}
  console.log('Contrôle boutons : ping ponctuel, reprise, échec, concurrence, retrait et API locale validés');
})().catch(e=>{console.error(e);process.exitCode=1});
