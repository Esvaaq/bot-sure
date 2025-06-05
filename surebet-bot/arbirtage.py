import json

def load_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def compute_arbitrage(sts_data: dict, fortuna_data: dict):
    wspolne_id = set(sts_data.keys()) & set(fortuna_data.keys())
    wynik = []
    for mid in sorted(wspolne_id):
        sts_match     = sts_data[mid]
        fortuna_match = fortuna_data[mid]

        # Zamieniamy listę markets na słownik "market|selection" → kurs (float)
        sts_offers = {
            f"{m['market']}|{m['selection']}": float(m["odds"].replace(",", "."))
            for m in sts_match["markets"]
        }
        fort_offers = {
            f"{m['market']}|{m['selection']}": float(m["odds"].replace(",", "."))
            for m in fortuna_match["markets"]
        }

        wspolne_oferty = set(sts_offers.keys()) & set(fort_offers.keys())
        if not wspolne_oferty:
            continue

        oferty = []
        for ofe in sorted(wspolne_oferty):
            kurs_sts  = sts_offers[ofe]
            kurs_fort = fort_offers[ofe]
            najlepszy = max(kurs_sts, kurs_fort)
            oferty.append((ofe, kurs_sts, kurs_fort, najlepszy))

        wynik.append({
            "match_id":   mid,
            "match_name": sts_match["match_name"],
            "datetime":   sts_match["datetime"],
            "offers":     oferty
        })
    return wynik

if __name__ == "__main__":
    sts_data     = load_json("sts_data.json")
    fortuna_data = load_json("fortuna_data.json")
    arb = compute_arbitrage(sts_data, fortuna_data)
    for item in arb:
        print(f"=== {item['match_id']} ===")
        print(f"{item['match_name']} ({item['datetime']}):")
        for of in item["offers"]:
            mkt_sel, s_odd, f_odd, best = of
            market, selection = mkt_sel.split("|")
            print(f"  → {market} → {selection} | STS={s_odd:.2f} | Fortuna={f_odd:.2f} | najlepszy={best:.2f}")
        print()
