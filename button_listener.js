#!/usr/bin/env node
/*
 * button_listener.js — rebinds the SoundTouch physical preset buttons (1-6)
 * to our local internet-radio (UPnP) playback, after the Bose cloud shutdown.
 *
 * How: the speaker exposes a WebSocket on :8080 (subprotocol "gabbo") that emits
 *   <updates><nowSelectionUpdated><preset id="N">...
 * the instant a physical preset button is pressed (before the dead cloud source
 * fails). We catch that, look up preset N in presets.json, and immediately push
 * our proxy stream to the speaker via UPnP SetAVTransportURI + Play.
 *
 * Run it long-lived (launchd / systemd / a scheduled task). Reconnects on drop.
 */
const fs   = require('fs');
const path = require('path');
const http = require('http');
const dgram = require('dgram');
const WebSocket = require('ws');

const CONFIG_DIR = path.resolve(process.env.BOSE_CONFIG_DIR || path.join(__dirname, 'config'));
const rd = (f, d) => { try { return JSON.parse(fs.readFileSync(path.join(CONFIG_DIR, f))); } catch { return d; } };

function proxyHost(cfg, speakerIp) {
  if (cfg.proxy_host && cfg.proxy_host !== 'auto') return Promise.resolve(cfg.proxy_host);
  return new Promise(res => {
    const s = dgram.createSocket('udp4');
    s.connect(9, speakerIp, () => { const a = s.address().address; s.close(); res(a); });
  });
}

function escapeXml(s){return s.replace(/[<>&'"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;',"'":'&apos;','"':'&quot;'}[c]));}

function upnpPlay(ip, proxyUrl, title) {
  const innerDidl =
    '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '+
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '+
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'+
    '<item id="0" parentID="-1" restricted="1">'+
    '<dc:title>'+escapeXml(title)+'</dc:title>'+
    '<upnp:class>object.item.audioItem.audioBroadcast</upnp:class>'+
    '<res protocolInfo="http-get:*:audio/mpeg:*">'+escapeXml(proxyUrl)+'</res>'+
    '</item></DIDL-Lite>';
  const didl = escapeXml(innerDidl);
  const soap = (action, body) => new Promise((resolve) => {
    const env = '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '+
      's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'+body+'</s:Body></s:Envelope>';
    const req = http.request({host:ip,port:8091,path:'/AVTransport/Control',method:'POST',
      headers:{'Content-Type':'text/xml; charset="utf-8"',
        'SOAPAction':'"urn:schemas-upnp-org:service:AVTransport:1#'+action+'"',
        'Content-Length':Buffer.byteLength(env)}},
      r => { r.on('data',()=>{}); r.on('end',resolve); });
    req.on('error',()=>resolve()); req.write(env); req.end();
  });
  return soap('SetAVTransportURI',
      '<u:SetAVTransportURI xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'+
      '<InstanceID>0</InstanceID><CurrentURI>'+escapeXml(proxyUrl)+'</CurrentURI>'+
      '<CurrentURIMetaData>'+didl+'</CurrentURIMetaData></u:SetAVTransportURI>')
    .then(()=>soap('Play','<u:Play xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'+
      '<InstanceID>0</InstanceID><Speed>1</Speed></u:Play>'));
}

function presetMapFor(name) {
  const presets = rd('presets.json', {});
  return presets[name] || {};
}

const sleep = ms => new Promise(r => setTimeout(r, ms));
const busyUntil = {};   // per-speaker cooldown to prevent re-trigger loops / double-press
let globalBusyUntil = 0; // when sync is on, a press in a zone echoes to ALL members;
                         // only the first should act, so the rest share one cooldown

// Force the speaker into INVALID_SOURCE by selecting a (dead) TuneIn item.
// This is the ONLY state the UPnP renderer can reliably take over from:
//  - TuneIn presets fail to INVALID_SOURCE on their own (3/4/6 worked),
//  - but Spotify presets hang in BUFFERING forever and block UPnP (1/2/5).
// Selecting a dead source first frees the audio engine for any button.
function forceInvalid(ip) {
  // Use a BOGUS station id (not any stored preset) so this select can't be
  // mistaken by the speaker for a preset press — that would loop us forever.
  const body = '<ContentItem source="TUNEIN" type="stationurl" '+
    'location="/v1/playback/station/s99999999" sourceAccount="" isPresetable="false">'+
    '<itemName>reset</itemName></ContentItem>';
  return new Promise(resolve => {
    const req = http.request({host: ip, port: 8090, path: '/select', method: 'POST',
      headers: {'Content-Type': 'text/xml', 'Content-Length': Buffer.byteLength(body)}},
      r => { r.on('data', () => {}); r.on('end', resolve); });
    req.on('error', () => resolve()); req.write(body); req.end();
  });
}

function get8090(ip, path) {
  return new Promise(resolve => {
    http.get({host: ip, port: 8090, path}, r => {
      let d = ''; r.on('data', c => d += c); r.on('end', () => resolve(d));
    }).on('error', () => resolve(''));
  });
}
function post8090(ip, path, body) {
  return new Promise(resolve => {
    const req = http.request({host: ip, port: 8090, path, method: 'POST',
      headers: {'Content-Type': 'text/xml', 'Content-Length': Buffer.byteLength(body)}},
      r => { r.on('data', () => {}); r.on('end', resolve); });
    req.on('error', () => resolve()); req.write(body); req.end();
  });
}
// Dissolve any existing zone the speaker belongs to. SetAVTransportURI returns
// HTTP 500 on a speaker that is a *zone member*, so we must tear the group down
// before re-playing — otherwise pressing a button while already synced fails.
async function dissolveZone(ip) {
  const speakers = rd('speakers.json', {});
  const byId = {}; for (const s of Object.values(speakers)) if (s.deviceID) byId[s.deviceID] = s;
  const xml = await get8090(ip, '/getZone');
  const mm = xml.match(/master="([0-9A-Fa-f]+)"/);
  if (!mm) return;                                  // not in a zone
  const master = byId[mm[1]]; if (!master) return;
  const members = [...xml.matchAll(/ipaddress="([^"]+)">([0-9A-Fa-f]+)</g)]
    .map(m => ({ip: m[1], deviceID: m[2]}));
  if (!members.length) return;
  let body = '<zone master="' + mm[1] + '">';
  for (const s of members) body += '<member ipaddress="' + s.ip + '">' + s.deviceID + '</member>';
  body += '</zone>';
  await post8090(master.ip, '/removeZone', body);
}

// Group all known speakers into one synchronized zone, with `masterName` as master.
// SoundTouch relays the master's UPnP stream to members as a single synced stream.
function buildZone(masterName, masterIp) {
  const speakers = rd('speakers.json', {});
  const master = speakers[masterName];
  const members = Object.values(speakers).filter(s => s.deviceID && s.ip);
  if (!master || !master.deviceID || members.length < 2) return Promise.resolve();
  let xml = '<zone master="' + master.deviceID + '">';
  for (const s of members) xml += '<member ipaddress="' + s.ip + '">' + s.deviceID + '</member>';
  xml += '</zone>';
  return new Promise(resolve => {
    const req = http.request({host: masterIp, port: 8090, path: '/setZone', method: 'POST',
      headers: {'Content-Type': 'text/xml', 'Content-Length': Buffer.byteLength(xml)}},
      r => { r.on('data', () => {}); r.on('end', resolve); });
    req.on('error', () => resolve()); req.write(xml); req.end();
  });
}

const connections = new Map();
function connect(name, ip, cfg, entry) {
  if (connections.get(name) !== entry) return;
  const url = 'ws://'+ip+':8080';
  const ws = new WebSocket(url, 'gabbo');
  entry.ws = ws;
  ws.on('open', () => console.log(`[${name}] listening (${ip})`));
  ws.on('message', async (data) => {
    if (connections.get(name) !== entry) return;
    const s = data.toString();
    const m = s.match(/<nowSelectionUpdated><preset id="(\d)"/);
    if (!m) return;
    const id = m[1];
    if (id === '0') return; // Ignore non-preset selection events
    const map = presetMapFor(name);
    const station = map[id];
    if (!station) { console.log(`[${name}] preset ${id} pressed — no mapping, ignored`); return; }
    const now = Date.now();
    if (busyUntil[name] && now < busyUntil[name]) return;   // same-speaker cooldown
    if (cfg.sync !== false && now < globalBusyUntil) {       // zone echo from another member
      console.log(`[${name}] preset ${id} — group cooldown, ignored (zone echo)`);
      return;
    }
    busyUntil[name] = now + 8000;
    if (cfg.sync !== false) globalBusyUntil = now + 8000;
    const host = await proxyHost(cfg, ip);
    const proxyUrl = 'http://'+host+':'+(cfg.proxy_port||8077)+'/stream/'+encodeURIComponent(station);
    console.log(`[${name}] preset ${id} -> ${station}  (${proxyUrl})`);
    try {
      if (cfg.sync !== false) await dissolveZone(ip);  // leave any old group (avoids member-500)
      await forceInvalid(ip);        // free the engine (handles stuck Spotify presets)
      await sleep(1500);
      await upnpPlay(ip, proxyUrl, station);
      if (cfg.sync !== false) {
        await sleep(1500);           // let master start before grouping
        await buildZone(name, ip);
        console.log(`[${name}] synced zone (master) -> all speakers`);
      }
    } catch(e){ console.log(`[${name}] play error`, e.message); }
  });
  ws.on('close', () => {
    if (connections.get(name) !== entry) return;
    console.log(`[${name}] closed; reconnecting in 5s`);
    entry.timer = setTimeout(()=>connect(name,ip,cfg,entry),5000);
  });
  ws.on('error', (e) => { console.log(`[${name}] ws error: ${e.message}`); try{ws.close();}catch{} });
}

function reloadSpeakers(){
  const cfg = rd('config.json', {proxy_port:8077, proxy_host:'auto'});
  const speakers = rd('speakers.json', null);
  if (!speakers || typeof speakers !== 'object' || Array.isArray(speakers)) return;
  for (const [name, entry] of connections) {
    if (speakers[name]?.ip === entry.ip && JSON.stringify(cfg) === entry.config) continue;
    connections.delete(name);clearTimeout(entry.timer);entry.ws?.terminate();
    delete busyUntil[name];
  }
  for (const [name, speaker] of Object.entries(speakers)) {
    if (!speaker?.ip || connections.has(name)) continue;
    const entry = {ip:speaker.ip,config:JSON.stringify(cfg)};
    connections.set(name,entry);connect(name,speaker.ip,cfg,entry);
  }
}
function main(){
  console.log('Bose button listener starting; watching speaker configuration');
  reloadSpeakers();setInterval(reloadSpeakers,2000);
}
main();
