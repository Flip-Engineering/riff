const fs = require('fs');
const path = require('path');
const input = process.argv[2], output = process.argv[3];
if (!input || !output || process.argv.length !== 4) {
  console.error('Usage: node abcjs_midi_to_file.js INPUT.abc OUTPUT.mid');
  process.exit(1);
}
const ABCJS = require(path.resolve(__dirname, '../../web/vendor/abcjs-basic-min.js'));
const abc = fs.readFileSync(input, 'utf8');
const r = ABCJS.synth.getMidiFile(abc, { midiOutputType: 'binary' });
console.log('ctor:', r && r.constructor && r.constructor.name, 'isUint8:', r instanceof Uint8Array, 'len:', r.length, 'keys:', Object.keys(r).slice(0,5));
const s = String(r);
const arr = s.split(',').map(Number);
console.log('string bytes:', arr.length, 'first:', arr.slice(0, 14).join(','));
fs.writeFileSync(output, Buffer.from(arr));
console.log('wrote via string path');
