import csv
from datetime import datetime

def load_csv(path: str):
    """
    Wczytuje dane z pliku CSV o strukturze:
    match_id, match_name, sport, competition, datetime, market, selection, odds, bookmaker

    Zwraca słownik:
    {
      match_id: {
        "match_name": str,
        "datetime":   str (ISO),
        "markets": [
          {"market": str, "selection": str, "odds": float},
          ...
        ]
      },
      ...
    }
    """
    data = {}
    with open(path, encoding="utf-8", newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            mid = row["match_id"]
            if not mid:
                continue

            # Parsujemy kurs na float (obsługa przecinka lub kropki)
            try:
                odds_val = float(row["odds"].replace(",", "."))
            except Exception:
                continue

            # Normalizacja do małych liter dla rynku i selekcji
            market = (row.get("market") or row.get("market_name", "")).strip().lower()
            selection = (row.get("selection") or row.get("outcome", "")).strip().lower()
            dt_str = row.get("datetime", "")

            if mid not in data:
                data[mid] = {
                    "match_name": row.get("match_name", ""),
                    "datetime":   dt_str,
                    "markets":    []
                }

            data[mid]["markets"].append({
                "market":    market,
                "selection": selection,
                "odds":      odds_val
            })

    return data


def compute_arbitrage(sts_data: dict, fortuna_data: dict):
    wspolne_id = set(sts_data.keys()) & set(fortuna_data.keys())
    wynik = []
    for mid in sorted(wspolne_id):
        sts_match     = sts_data[mid]
        fortuna_match = fortuna_data[mid]

        # Tworzymy słownik "market|selection" → kurs (float)
        sts_offers = {f"{m['market']}|{m['selection']}": m["odds"]
                      for m in sts_match["markets"]}
        fort_offers = {f"{m['market']}|{m['selection']}": m["odds"]
                       for m in fortuna_match["markets"]}

        wspolne_oferty = set(sts_offers.keys()) & set(fort_offers.keys())
        if not wspolne_oferty:
            continue

        oferty = []
        for ofe in sorted(wspolne_oferty):
            kurs_sts  = sts_offers[ofe]
            kurs_fort = fort_offers[ofe]
            # Określamy, który bukmacher daje kurs lepszy (lub oba, gdy równe)
            if kurs_sts > kurs_fort:
                best_bm = "STS"
                najlepszy = kurs_sts
            elif kurs_fort > kurs_sts:
                best_bm = "Fortuna"
                najlepszy = kurs_fort
            else:
                best_bm = "STS/Fortuna"
                najlepszy = kurs_sts  # równe

            oferty.append((ofe, kurs_sts, kurs_fort, najlepszy, best_bm))

        wynik.append({
            "match_id":   mid,
            "match_name": sts_match["match_name"],
            "datetime":   sts_match["datetime"],
            "offers":     oferty
        })
    return wynik


if __name__ == "__main__":
    sts_data     = load_csv("sts_data.csv")
    fortuna_data = load_csv("fortuna_data.csv")
    arb = compute_arbitrage(sts_data, fortuna_data)
    for item in arb:
        # Zamieniamy 'T' na spację, by wyświetlić "YYYY-MM-DD HH:MM:SS"
        dt_display = item["datetime"].replace("T", " ")
        print(f"=== {item['match_id']} ===")
        print(f"{item['match_name']} ({dt_display}):")
        for of in item["offers"]:
            mkt_sel, s_odd, f_odd, best, best_bm = of
            market, selection = mkt_sel.split("|", 1)
            print(
                f"  → {market} → {selection} "
                f"| STS={s_odd:.2f} | Fortuna={f_odd:.2f} "
                f"| najlepszy={best:.2f} ({best_bm})"
            )
        print()
