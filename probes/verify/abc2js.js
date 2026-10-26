const fs=require('fs'), vm=require('vm'), path=require('path');
const input=process.argv[2], out=process.argv[3];
if (!input || !out || process.argv.length !== 4) {
  console.error('Usage: node abc2js.js INPUT.abc OUTPUT.mid');
  process.exit(1);
}
const code=fs.readFileSync(path.resolve(__dirname,'../..','web/vendor/abcjs-basic-min.js'),'utf8');
const sandbox={window:{},document:{createElement:()=>({}),documentElement:{}},navigator:{userAgent:'node'},console};
sandbox.window=sandbox; sandbox.globalThis=sandbox; vm.createContext(sandbox); vm.runInContext(code,sandbox,{timeout:30000});
const ABCJS=sandbox.ABCJS;
const abc=fs.readFileSync(input,'utf8');
try {
  const files=ABCJS.synth.getMidiFile(abc,{midiOutputType:'binary'});
  console.log('files', files.length, files.map(f=>[typeof f, f && f.constructor && f.constructor.name, f && Object.keys(f).slice(0,5), f && f.length]));
  let data=files[0];
  if (data && data.data) data=data.data;
  if (typeof data==='object' && !(data instanceof Uint8Array)) data=Uint8Array.from(data);
  fs.writeFileSync(out, Buffer.from(data));
  console.log('wrote', out, fs.statSync(out).size);
} catch(e) { console.error('ERR', e && e.stack || e); process.exit(1); }
