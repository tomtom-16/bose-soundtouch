// Contrôle ponctuel des WebSockets ; aucun ping périodique.
const http = require('http');
const crypto = require('crypto');

function probe(ws, timeout=3000) {
  if (!ws || ws.readyState > 1) return Promise.resolve(false);
  return new Promise(resolve=>{
    const token=crypto.randomBytes(12).toString('hex');
    const finish=ok=>{
      clearTimeout(timer);
      ws.removeListener('open',ping);ws.removeListener('pong',pong);
      ws.removeListener('close',closed);ws.removeListener('error',closed);
      resolve(ok);
    };
    const closed=()=>finish(false);
    const pong=data=>{if(data.toString()===token)finish(true)};
    const ping=()=>{try{ws.ping(token)}catch{finish(false)}};
    const timer=setTimeout(closed,timeout);
    ws.on('pong',pong);ws.on('close',closed);ws.on('error',closed);
    if(ws.readyState===1)ping();else ws.on('open',ping);
  });
}

function createControl({connections,reloadSpeakers,reconnect}, verify=probe) {
  const snapshot=()=>({ok:true,available:true,speakers:Object.fromEntries(
    [...connections].map(([name,e])=>[name,{ip:e.ip,state:e.state||'disconnected'}]))});
  function check(name,entry) {
    if(entry.checking)return entry.checking;
    // La promesse est installée avant toute fermeture ou reconnexion.
    entry.checking=Promise.resolve().then(async()=>{
      clearTimeout(entry.timer);entry.paused=false;
      const current=()=>connections.get(name)===entry;
      try{
        let ok=await verify(entry.ws);
        if(!current())return;
        if(!ok){
          const old=entry.ws;entry.ws=null;old?.terminate();
          reconnect(name,entry);
          ok=await verify(entry.ws);
        }
        if(!current())return;
        entry.state=ok?'connected':'disconnected';
        entry.paused=!ok;
        if(!ok){const old=entry.ws;entry.ws=null;old?.terminate()}
      }finally{entry.checking=null}
    });
    return entry.checking;
  }
  async function checkAll(name) {
    reloadSpeakers();
    if(name!==undefined&&!connections.has(name))throw Error('Enceinte inconnue.');
    await Promise.all([...connections].filter(([key])=>name===undefined||key===name)
      .map(([key,entry])=>check(key,entry)));
    return snapshot();
  }
  return {snapshot,checkAll};
}

function startControl(options) {
  const control=createControl(options);
  const server=http.createServer(async(req,res)=>{
    const send=(value,status=200)=>{
      res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});
      res.end(JSON.stringify(value));
    };
    // Pas d'accès navigateur direct, même depuis une page d'un autre site.
    if(req.headers.origin || req.headers.host!==`127.0.0.1:${server.address().port}`)
      return send({ok:false,error:'Accès refusé.'},403);
    if(req.method==='GET'&&req.url==='/status')return send(control.snapshot());
    let url;
    try{url=new URL(req.url,'http://localhost')}catch{return send({ok:false},400)}
    if(req.method!=='POST'||url.pathname!=='/check')return send({ok:false},404);
    try{send(await control.checkAll(url.searchParams.has('name')?url.searchParams.get('name'):undefined))}
    catch(e){send({ok:false,error:e.message},400)}
  });
  server.requestTimeout=10000;server.headersTimeout=10000;
  server.on('error',e=>{console.error('Contrôle local des boutons indisponible : '+e.message);process.exit(1);});
  server.listen(Number(process.env.BOSE_BUTTONS_PORT||8078),'127.0.0.1');
  return server;
}
module.exports={probe,createControl,startControl};
