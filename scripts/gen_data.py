"""Pipeline dati dashboard commesse: Excel + Google Sheet pianificazione -> JSON in chiaro.

Legge i timesheet e Elenco_commesse.xlsx da DATA_DIR, scarica la pianificazione
pubblicata (stessa fonte usata dal fetch live lato browser) e scrive
build/data.plain.json con la stessa forma del blocco dashData storico di
Commesse.html. L'output NON va mai committato in chiaro: scripts/encrypt.js lo
consuma e lo sostituisce con docs/data.enc.json.
"""
import json
import re
import unicodedata
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA_DIR = Path(r"G:\Il mio Drive\Calendari\Calendari_2026\TS")
ELENCO_COMMESSE = DATA_DIR / "Elenco_commesse.xlsx"
OUTPUT = Path(__file__).resolve().parent.parent / "build" / "data.plain.json"

DIVISIONE = "M&E"
GSHEET_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vSp37Wg1X6gfecCfjHogkrAdrmMOpg5c6_r5FwhTmhCunD9gWHR7bpCKo6TtHWwsT2vcrySZ4tZucF2"
    "/pub?gid=1328021922&single=true&output=csv"
)
MONTHS_IT = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
GSHEET_MONTH_COLS = ["GEN", "FEB", "MAR", "APR", "MAG", "GIU", "LUG", "AGO", "SET", "OTT", "NOV", "DIC"]


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return s.strip().lower()


def name_key(nome, cognome):
    nome, cognome = str(nome).strip(), str(cognome).strip()
    first = norm(nome.split()[0]) if nome else ""
    last = norm(cognome.split()[-1]) if cognome else ""
    return (first, last)


def key_from_full_name(full_name):
    toks = str(full_name).split()
    if not toks:
        return ("", "")
    return (norm(toks[0]), norm(toks[-1]))


def extract_code(progetto):
    m = re.match(r"^\s*([A-Za-z]+)\s*([0-9]+)", str(progetto))
    if not m:
        return None
    return m.group(1).upper() + m.group(2)


def load_timesheet():
    files = [f for f in DATA_DIR.glob("*.xlsx") if f.name != ELENCO_COMMESSE.name]
    if not files:
        raise SystemExit(f"Nessun file timesheet trovato in {DATA_DIR}")
    frames = [pd.read_excel(f, sheet_name=0) for f in files]
    ts = pd.concat(frames, ignore_index=True)
    ts = ts[ts["Data Registrazione"].notna()].copy()
    # Solo righe approvate: le altre (bozza, in attesa) non sono consuntivi affidabili.
    ts = ts[ts["Stato Timesheet"].astype(str).str.strip() == "Approvato"].copy()
    ts = ts.drop_duplicates()
    ts["code"] = ts["Nome Progetto"].apply(extract_code)
    ts["data_dt"] = pd.to_datetime(ts["Data Registrazione"])

    # Il mese in corso non e' mai completamente consuntivato negli Excel: si
    # ferma al mese precedente (incluso) e da qui in poi (mese corrente in
    # poi) si affida al pianificato del gsheet, vedi apply_pianificato_baseline.
    now = datetime.now()
    cutoff = pd.Timestamp(now.year, now.month, 1)
    ts = ts[ts["data_dt"] < cutoff]

    ts["persona_nome"] = ts["Nome Proprio"].astype(str).str.strip() + " " + ts["Cognome"].astype(str).str.strip()
    ts["persona_key"] = [name_key(n, c) for n, c in zip(ts["Nome Proprio"], ts["Cognome"])]
    ts["ore"] = ts["Durata"].fillna(0).astype(float)
    ts["nota"] = ts["Note"].fillna("").astype(str)
    return ts


def load_elenco():
    df = pd.read_excel(ELENCO_COMMESSE, sheet_name=0)
    df = df.rename(columns={"Monte Ore": "MonteOre"})
    return df


def build_person_resolution(ts):
    """Persone note dal timesheet: persona_key -> nome canonico (Nome + Cognome)."""
    known = {}
    for key, nome in zip(ts["persona_key"], ts["persona_nome"]):
        known.setdefault(key, nome)
    return known


def month_label(dt):
    return f"{MONTHS_IT[dt.month - 1].capitalize()}-{str(dt.year)[2:]}"


def build_serie(rows):
    """rows: DataFrame con colonne data_dt/ore, aggrega per giorno e cumula."""
    if rows.empty:
        return []
    by_day = rows.groupby(rows["data_dt"].dt.date)["ore"].sum().sort_index()
    serie = []
    cum = 0.0
    for d, h in by_day.items():
        cum = round(cum + h, 2)
        serie.append({"d": d.isoformat(), "h": round(float(h), 2), "c": cum})
    return serie


def build_mensile_ytd(rows, anno_corrente):
    """rows filtrate per persona+commessa: drill-down mensile dell'anno corrente.

    Ogni voce del drill-down corrisponde a una singola riga di timesheet (non
    aggregata per giorno): la stessa giornata puo' avere piu' righe (es. ore
    lavorate + una nota a ore zero), e vanno mostrate entrambe separatamente,
    coerentemente con l'export originale.
    """
    rows_year = rows[rows["data_dt"].dt.year == anno_corrente].sort_values("data_dt", kind="stable")
    if rows_year.empty:
        return []
    by_month = defaultdict(lambda: {"ore": 0.0, "voci": [], "_label": None})
    for _, row in rows_year.iterrows():
        dt = row["data_dt"]
        mkey = (dt.year, dt.month)
        m = by_month[mkey]
        m["ore"] = round(m["ore"] + row["ore"], 2)
        m["voci"].append({"data": dt.date().isoformat(), "ore": round(float(row["ore"]), 2), "nota": row["nota"]})
        m["_label"] = month_label(dt)
    out = []
    for mkey in sorted(by_month.keys()):
        m = by_month[mkey]
        out.append({"mese": m["_label"], "ore": m["ore"], "voci": m["voci"]})
    return out


def build_commessa_record(code, code_rows, anno_corrente, meta_extra=None):
    """Costruisce il record aggregato (serie, per-persona, ecc.) comune a JSB e BWV."""
    ore_consuntivate = round(float(code_rows["ore"].sum()), 2)
    ore_ytd = round(float(code_rows.loc[code_rows["data_dt"].dt.year == anno_corrente, "ore"].sum()), 2)
    prima_data = code_rows["data_dt"].min()
    ultima_data = code_rows["data_dt"].max()
    serie = build_serie(code_rows)

    per_persona_real = {}
    for key, grp in code_rows.groupby("persona_key"):
        ore = round(float(grp["ore"].sum()), 2)
        per_persona_real[key] = {
            "nome": grp["persona_nome"].iloc[0],
            "ore": ore,
            "mensile_ytd": build_mensile_ytd(grp, anno_corrente),
        }
    n_risorse_reali = sum(1 for p in per_persona_real.values() if p["ore"] > 0)

    rec = {
        "code": code,
        "descrizione": "",
        "ore_consuntivate": ore_consuntivate,
        "ore_ytd": ore_ytd,
        "n_risorse_reali": n_risorse_reali,
        "prima_data": prima_data.date().isoformat() if pd.notna(prima_data) else None,
        "ultima_data": ultima_data.date().isoformat() if pd.notna(ultima_data) else None,
        "serie": serie,
        "_per_persona_real": per_persona_real,
    }
    if meta_extra:
        rec.update(meta_extra)
    return rec


def main():
    ts = load_timesheet()
    elenco = load_elenco()
    anno_corrente = datetime.now().year

    known_persons = build_person_resolution(ts)

    # ---------- risoluzione Risorsa (Elenco_commesse) -> persona timesheet ----------
    risorse_uniche = sorted(set(elenco["Risorsa"].dropna().astype(str).str.strip()))
    non_risolte = [r for r in risorse_uniche if key_from_full_name(r) not in known_persons]

    elenco_per_commessa = defaultdict(list)  # code -> list of (risorsa_key, monte_ore, risolto)
    manager_by_code, nome_by_code, stato_by_code = {}, {}, {}
    for _, row in elenco.iterrows():
        code = str(row["Commessa"]).strip()
        risorsa = str(row["Risorsa"]).strip() if pd.notna(row["Risorsa"]) else ""
        key = key_from_full_name(risorsa) if risorsa else None
        monte_ore = float(row["MonteOre"]) if pd.notna(row["MonteOre"]) else 0.0
        if key:
            elenco_per_commessa[code].append({
                "key": key,
                "risorsa_nome": risorsa,
                "monte_ore": monte_ore,
                "risolto": key in known_persons,
            })
        if code not in manager_by_code and pd.notna(row.get("Manager")):
            manager_by_code[code] = str(row["Manager"]).strip()
        if code not in nome_by_code and pd.notna(row.get("Nome")):
            nome_by_code[code] = str(row["Nome"]).strip()
        if code not in stato_by_code and pd.notna(row.get("Stato")):
            stato_by_code[code] = str(row["Stato"]).strip()

    jsb_codes = sorted(elenco_per_commessa.keys() | set(manager_by_code) | set(nome_by_code) | set(stato_by_code))
    jsb_codes = [c for c in jsb_codes if c.upper().startswith("JSB")]

    # ---------- monte ore complessivo per commessa (somma per persona) ----------
    monte_ore_commessa = {
        code: round(sum(r["monte_ore"] for r in rows), 2) for code, rows in elenco_per_commessa.items()
    }

    ts_jsb = ts[ts["code"].isin(jsb_codes)]

    jsb_records = []
    all_resolved_person_keys = set()
    for code in jsb_codes:
        code_rows = ts_jsb[ts_jsb["code"] == code]
        rec = build_commessa_record(code, code_rows, anno_corrente)
        rec["manager"] = manager_by_code.get(code)
        rec["descrizione"] = nome_by_code.get(code, "")
        rec["stato"] = stato_by_code.get(code, "Aperta")
        rec["cliente"] = guess_cliente(code_rows)
        rec["divisione"] = DIVISIONE
        rec["monte_ore"] = monte_ore_commessa.get(code, 0.0)
        rec["pianificato"] = 0.0
        rec["totale"] = rec["ore_consuntivate"]
        rec["pct"] = round(rec["totale"] / rec["monte_ore"] * 100, 1) if rec["monte_ore"] > 0 else None

        per_persona_real = rec.pop("_per_persona_real")
        merged = dict(per_persona_real)
        for assign in elenco_per_commessa.get(code, []):
            key = assign["key"]
            if key in merged:
                p = merged[key]
                p["monte_ore"] = assign["monte_ore"]
                p["pianificato"] = True
                p["risolto"] = assign["risolto"]
            else:
                merged[key] = {
                    "nome": known_persons.get(key, assign["risorsa_nome"]),
                    "ore": 0.0,
                    "mensile_ytd": [],
                    "monte_ore": assign["monte_ore"],
                    "pianificato": True,
                    "risolto": assign["risolto"],
                }
        for key, p in merged.items():
            p.setdefault("monte_ore", 0.0)
            p.setdefault("pianificato", False)
            p.setdefault("risolto", True)
            p["ore_pianificate"] = 0.0
            if p["risolto"]:
                all_resolved_person_keys.add(key)
        rec["per_persona"] = sorted(merged.values(), key=lambda p: -p["ore"])
        jsb_records.append(rec)

    # ---------- BWV: solo persone d'interesse ----------
    persone_su_jsb_tracciate = set(ts_jsb["persona_key"].unique())
    persone_elenco_risolte = {
        assign["key"] for rows in elenco_per_commessa.values() for assign in rows if assign["risolto"]
    }
    people_of_interest = persone_su_jsb_tracciate | persone_elenco_risolte

    ts_bwv = ts[(ts["code"].notna()) & (ts["code"].str.upper().str.startswith("BWV")) & (ts["persona_key"].isin(people_of_interest))]
    bwv_codes = sorted(ts_bwv["code"].unique())

    bwv_records = []
    for code in bwv_codes:
        code_rows = ts_bwv[ts_bwv["code"] == code]
        rec = build_commessa_record(code, code_rows, anno_corrente)
        rec["descrizione"] = guess_descrizione_bwv(code_rows)
        per_persona_real = rec.pop("_per_persona_real")
        for key, p in per_persona_real.items():
            if p["risolto"] if "risolto" in p else True:
                all_resolved_person_keys.add(key)
        rec["per_persona"] = sorted(per_persona_real.values(), key=lambda p: -p["ore"])
        bwv_records.append(rec)

    # ---------- pianificato: baseline statica da gsheet (fallback se il fetch live fallisce) ----------
    apply_pianificato_baseline(jsb_records, jsb_codes, anno_corrente)

    pm_list = sorted({m for m in manager_by_code.values() if m})

    meta = {
        "generato_il": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "anno_corrente": anno_corrente,
        "totale_ore_jsb": round(sum(r["ore_consuntivate"] for r in jsb_records), 2),
        "totale_ore_ytd": round(sum(r["ore_ytd"] for r in jsb_records), 2),
        "totale_pianificato": round(sum(r["pianificato"] for r in jsb_records), 2),
        "totale_monte_ore": round(sum(r["monte_ore"] for r in jsb_records), 2),
        "totale_ore_bwv": round(sum(r["ore_consuntivate"] for r in bwv_records), 2),
        "n_commesse_jsb": len(jsb_records),
        "n_commesse_bwv": len(bwv_records),
        "n_persone": len(all_resolved_person_keys),
        "n_risorse_cartel1_non_risolte": len(non_risolte),
        "data_min": ts["data_dt"].min().date().isoformat(),
        "data_max": ts["data_dt"].max().date().isoformat(),
        "pm_list": pm_list,
        "gsheet_csv_url": GSHEET_CSV_URL,
    }

    for rec in jsb_records + bwv_records:
        rec.pop("code", None)  # ripristinato sotto, ordine chiavi non rilevante
    for rec, code in zip(jsb_records, jsb_codes):
        rec["code"] = code
    for rec, code in zip(bwv_records, bwv_codes):
        rec["code"] = code

    output = {"meta": meta, "jsb": jsb_records, "bwv": bwv_records}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False)
    print(f"Scritto {OUTPUT} — {len(jsb_records)} commesse JSB, {len(bwv_records)} voci BWV, "
          f"{meta['n_persone']} persone, non risolte: {non_risolte}")


CLIENT_ALIASES = {
    "chiesi": "Chiesi Farmaceutici Spa",
    "belletti": "Belletti e Ferrari",
}
# Cliente dominante del book of business: usato quando il testo del progetto
# non riporta esplicitamente un nome cliente riconosciuto (es. "JSB2152
# Supporto gestione DUVRI R&D SE - 2026").
CLIENT_DEFAULT = "Chiesi Farmaceutici Spa"


def guess_cliente(code_rows):
    if code_rows.empty:
        return None
    testo = str(code_rows["Nome Progetto"].dropna().iloc[0]).strip()
    resto = re.sub(r"^\s*[A-Za-z]+\s*[0-9]+\s*", "", testo)
    if not resto:
        return None
    primo = resto.split()[0]
    return CLIENT_ALIASES.get(norm(primo), CLIENT_DEFAULT)


def guess_descrizione_bwv(code_rows):
    if code_rows.empty:
        return ""
    testo = str(code_rows["Nome Progetto"].dropna().iloc[0]).strip()
    return re.sub(r"^\s*[A-Za-z]+\s*[0-9]+\s*", "", testo).strip()


def to_num(v):
    try:
        return float(str(v).strip().replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def apply_pianificato_baseline(jsb_records, jsb_codes, anno_corrente):
    try:
        with urllib.request.urlopen(GSHEET_CSV_URL, timeout=20) as resp:
            text = resp.read().decode("utf-8")
    except Exception as e:
        print(f"Avviso: fetch pianificato fallito ({e}), pianificato resta a 0 per tutte le commesse.")
        return

    import csv
    import io

    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return
    header = rows[0]
    try:
        idx_risorsa = header.index("Risorsa")
        idx_commessa = header.index("Commessa")
        month_col = [header.index(m) for m in GSHEET_MONTH_COLS]
    except ValueError:
        print("Avviso: formato foglio pianificazione inatteso, salto il baseline.")
        return

    now = datetime.now()
    # Complementare al cutoff di load_timesheet(): i consuntivi Excel si fermano
    # al mese precedente, il pianificato copre dal mese corrente in poi.
    from_month = now.month - 1 if now.year == anno_corrente else 0
    jsb_code_set = set(jsb_codes)

    by_commessa = defaultdict(float)
    by_commessa_persona = defaultdict(float)
    for r in rows[1:]:
        if len(r) <= max(idx_commessa, idx_risorsa, *month_col):
            continue
        m = re.match(r"^\s*(JSB\d+)", str(r[idx_commessa]))
        if not m or m.group(1) not in jsb_code_set:
            continue
        code = m.group(1)
        future_days = sum(to_num(r[c]) for c in month_col[from_month:])
        future_hours = future_days * 8
        if future_hours == 0:
            continue
        full = str(r[idx_risorsa]).strip()
        if "," in full:
            cog, nome = full.split(",", 1)
            full = f"{nome.strip()} {cog.strip()}"
        key = key_from_full_name(full)
        by_commessa[code] += future_hours
        by_commessa_persona[(code, key)] += future_hours

    for rec in jsb_records:
        p = round(by_commessa.get(rec["code"], 0.0), 2)
        rec["pianificato"] = p
        rec["totale"] = round(rec["ore_consuntivate"] + p, 2)
        rec["pct"] = round(rec["totale"] / rec["monte_ore"] * 100, 1) if rec["monte_ore"] > 0 else None
        for pp in rec["per_persona"]:
            pkey = key_from_full_name(pp["nome"])
            pp["ore_pianificate"] = round(by_commessa_persona.get((rec["code"], pkey), 0.0), 2)


if __name__ == "__main__":
    main()
