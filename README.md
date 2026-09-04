# Avanzamento commesse

Dashboard ore/budget delle commesse, pubblicata su GitHub Pages con dati
cifrati lato client (nessun dato in chiaro nel repository pubblico).

https://gioconoscenti.github.io/Avanzamento_commesse/

## Come pubblicare un aggiornamento

1. Copia i nuovi export Excel (timesheet + `Elenco_commesse.xlsx`) dentro
   `Avanzamento_scarichi\TS` (cartella accanto a questa), sostituendo quelli
   vecchi.
2. Fai doppio click su `publish.bat` in questa cartella.
3. Inserisci la password di cifratura quando richiesta (due volte, per
   conferma). È la stessa password che useranno le persone che consultano
   la dashboard — comunicala privatamente, non finisce mai nel repository.
4. Attendi il messaggio finale con il link pubblico: la pagina è aggiornata
   in genere entro un minuto (tempo di build di GitHub Pages).

Lo script (`scripts/publish.ps1`) fa tutto da solo: rigenera i dati dagli
Excel, li cifra, pulisce i file temporanei e pubblica su GitHub. Include un
controllo che blocca il commit se per errore finiscono in staging file Excel
o dati in chiaro.

## Struttura

- `docs/index.html` — la dashboard (gate password + rendering).
- `docs/data.enc.json` — unico dato committato: JSON cifrato
  (PBKDF2-SHA256 + AES-GCM, 600.000 iterazioni).
- `scripts/gen_data.py` — legge gli Excel e la pianificazione, produce il
  JSON in chiaro (mai committato).
- `scripts/encrypt.js` — cifra il JSON in chiaro in `docs/data.enc.json`.
- `scripts/publish.ps1` — orchestratore invocato da `publish.bat`.

## Sicurezza

Il repository è pubblico: chiunque conosca l'URL può scaricare
`docs/data.enc.json`, ma senza la password non può leggerne il contenuto
(cifratura reale, non un gate cosmetico). La password non è mai salvata nel
browser (niente localStorage/sessionStorage): va reinserita a ogni sessione.
