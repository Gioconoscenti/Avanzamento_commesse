// Cifra build/data.plain.json -> docs/data.enc.json con PBKDF2(SHA-256) + AES-GCM.
// Password letta due volte da stdin con input mascherato (nessun eco a schermo).
'use strict';
const fs = require('fs');
const path = require('path');
const { webcrypto } = require('crypto');
const { subtle } = webcrypto;

const ITERATIONS = 600000;

let stdinLines = null;
let stdinLineIdx = 0;
function readLineNoTty(prompt) {
  // Fallback quando stdin non e' un terminale (es. input pipato/rediretto):
  // niente mascheramento possibile, ma lo script resta utilizzabile per
  // test/automazione. Lettura sincrona in blocco (readline su stream non
  // interattivi ha un lifecycle fragile con piu' question() consecutive).
  if (stdinLines === null) {
    stdinLines = fs.readFileSync(0, 'utf8').split(/\r?\n/);
  }
  process.stdout.write(prompt);
  const line = stdinLines[stdinLineIdx] || '';
  stdinLineIdx += 1;
  return Promise.resolve(line);
}

function readMaskedPassword(prompt) {
  if (!process.stdin.isTTY) {
    return readLineNoTty(prompt);
  }
  return new Promise((resolve) => {
    process.stdout.write(prompt);
    const stdin = process.stdin;
    const wasRaw = stdin.isRaw;
    stdin.setRawMode(true);
    stdin.resume();
    stdin.setEncoding('utf8');
    let value = '';
    const onData = (ch) => {
      if (ch === '') { // Ctrl+C
        process.stdout.write('\n');
        process.exit(1);
      } else if (ch === '\r' || ch === '\n') {
        stdin.setRawMode(wasRaw);
        stdin.pause();
        stdin.removeListener('data', onData);
        process.stdout.write('\n');
        resolve(value);
      } else if (ch === '' || ch === '\b') { // backspace
        value = value.slice(0, -1);
      } else {
        value += ch;
      }
    };
    stdin.on('data', onData);
  });
}

function b64(buf) {
  return Buffer.from(buf).toString('base64');
}

async function deriveKey(password, salt) {
  const baseKey = await subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey']);
  return subtle.deriveKey(
    { name: 'PBKDF2', salt, iterations: ITERATIONS, hash: 'SHA-256' },
    baseKey,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt']
  );
}

async function main() {
  const [, , inputArg, outputArg] = process.argv;
  if (!inputArg || !outputArg) {
    console.error('Uso: node encrypt.js <input.json> <output.enc.json>');
    process.exit(1);
  }
  const inputPath = path.resolve(inputArg);
  const outputPath = path.resolve(outputArg);
  const plaintext = fs.readFileSync(inputPath, 'utf8');

  let password = await readMaskedPassword('Password di cifratura (min 8 caratteri): ');
  if (password.length < 8) {
    console.error('Password troppo corta (minimo 8 caratteri).');
    process.exit(1);
  }
  const confirm = await readMaskedPassword('Conferma password: ');
  if (confirm !== password) {
    console.error('Le due password non coincidono.');
    process.exit(1);
  }

  const salt = webcrypto.getRandomValues(new Uint8Array(16));
  const iv = webcrypto.getRandomValues(new Uint8Array(12));
  const key = await deriveKey(password, salt);
  const ciphertext = await subtle.encrypt({ name: 'AES-GCM', iv }, key, new TextEncoder().encode(plaintext));

  const envelope = {
    v: 1,
    kdf: 'PBKDF2',
    hash: 'SHA-256',
    iterations: ITERATIONS,
    cipher: 'AES-GCM',
    salt: b64(salt),
    iv: b64(iv),
    ciphertext: b64(ciphertext),
  };

  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, JSON.stringify(envelope));
  console.log(`Scritto ${outputPath} (${fs.statSync(outputPath).size} byte cifrati).`);
}

main().catch((e) => {
  console.error('Errore:', e);
  process.exit(1);
});
