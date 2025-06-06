import re
import csv
from datetime import datetime

# 🔧 Ustawienia:
minimal_profit = 0.0   # zwróć też zerowe sąsiedztwo (break-even), >0 wyklucza dokładnie 0%
force_show_all = False # pomijamy lamaki, rynki z !=2 selekcjami i low‐profit

def load_csv(path: str):
    data = {}
    with open(path, encoding="utf-8", newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            mid = row["match_id"]
            if not mid:
                continue
            try:
                odds_val = float(row["odds"].replace(",", "."))
            except:
                continue

            market    = (row.get("market") or row.get("market_name", "")).strip().lower()
            selection = (row.get("selection") or row.get("outcome", "")).strip().lower()
            dt_str    = row.get("datetime", "")
            sport     = row.get("sport", "")
            bookmaker = row.get("bookmaker", "")
            league    = row.get("competition", "")

            if mid not in data:
                data[mid] = {
                    "match_name": row.get("match_name", ""),
                    "datetime":   dt_str,
                    "sport":      sport,
                    "league":     league,
                    "markets":    []
                }

            data[mid]["markets"].append({
                "market":    market,
                "selection": selection,
                "odds":      odds_val,
                "bookmaker": bookmaker
            })

    return data

def compute_profit_with_tax(odds1_raw, odds2_raw):
    odds1 = odds1_raw * 0.88
    odds2 = odds2_raw * 0.88

    inv1 = 1 / odds1
    inv2 = 1 / odds2
    total_inv = inv1 + inv2

    stake  = 100
    stake1 = stake * inv1 / total_inv
    stake2 = stake * inv2 / total_inv

    payout1 = stake1 * odds1
    payout2 = stake2 * odds2

    profit = min(payout1, payout2) - stake
    return round(profit / stake * 100, 2)

def extract_submarket(raw_market, raw_selection):
    """
    Zwraca (submarket, sel_key) tylko wtedy, gdy mamy JASNY dwuwyborowy rynek:
      – handicap: raw_market zawiera "handicap" i raw_selection ma "(±X.Y)"
      – over/under: raw_market zawiera "over" lub "under" (lub "powyżej"/"poniżej") 
                    i raw_selection zaczyna się od "over"/"under"/"powyżej"/"poniżej"
      – BTTS / DNB / inne naturalne pary: raw_selection to dokładnie "tak"/"nie" lub "1"/"2".
    W innym wypadku zwraca (None, None).
    """
    mkt = raw_market.lower().strip()
    sel = raw_selection.lower().strip()

    # 1) HANDICAP → raw_market musi zawierać "handicap"
    if "handicap" in mkt:
        # w selekcji musi być "(+X.Y)" lub "(-X.Y)"
        m = re.search(r"\(([+-]?\d+(\.\d+)?)\)", sel)
        if not m:
            return None, None
        line = m.group(1)                  # np. "+1.5" lub "-0.5"
        subm = f"handicap:{line}"          # np. "handicap:+1.5"
        sel_key = sel.split()[0]           # "1" lub "2"
        return subm, sel_key

    # 2) OVER/UNDER → raw_market zawiera słowo "over" lub "under" (ENG) 
    #               lub "powyżej"/"poniżej" (PL)
    if ("over" in mkt or "under" in mkt) or ("powyżej" in mkt or "poniżej" in mkt):
        # selekcja musi zaczynać się od "over"/"under"/"powyżej"/"poniżej"
        if sel.startswith("over") or sel.startswith("powyżej") \
           or sel.startswith("under") or sel.startswith("poniżej"):
            # wyciągamy liczbę w selekcji, np. "2.5"
            m2 = re.search(r"(\d+(\.\d+)?)", sel)
            if not m2:
                return None, None
            line = m2.group(1)            # np. "2.5"
            subm = f"over_under:{line}"   # np. "over_under:2.5"
            if sel.startswith("over") or sel.startswith("powyżej"):
                sel_key = "over"
            else:
                sel_key = "under"
            return subm, sel_key
        else:
            return None, None

    # 3) BTTS / DNB / inne naturalne pary → selekcja to dokładnie "tak"/"nie" lub "1"/"2"
    if sel in ("tak", "nie") or sel in ("1", "2"):
        return mkt, sel

    # INNE PRZYPADKI POMIŃ
    return None, None

def compute_surebets(sts_data, fortuna_data):
    wspolne_id = set(sts_data.keys()) & set(fortuna_data.keys())
    surebets   = []

    for mid in sorted(wspolne_id):
        sts  = sts_data[mid]
        fort = fortuna_data[mid]

        combined = {}
        # 1) Połącz STS + Fortuna, ale tylko dozwolone submarkety:
        for entry in sts["markets"] + fort["markets"]:
            submkt, sel_key = extract_submarket(entry["market"], entry["selection"])
            if submkt is None:
                continue
            combined.setdefault((submkt, sel_key), []).append(
                (entry["bookmaker"], entry["odds"])
            )

        # 2) Grupuj po submarket:
        grouped_by_submkt = {}
        for (submkt, sel_key), offers in combined.items():
            grouped_by_submkt.setdefault(submkt, {})[sel_key] = offers

        # 3) Dla każdego submarketu: musi być dokładnie 2 unikalne sel_key
        for submkt, selections in grouped_by_submkt.items():
            if len(selections) != 2 and not force_show_all:
                continue

            items = list(selections.items())
            # jeśli wciąż <2 selekcji, pomijamy:
            if len(items) < 2:
                continue

            best_1 = max(items[0][1], key=lambda x: x[1])  # (book, odds)
            best_2 = max(items[1][1], key=lambda x: x[1])

            # lamak? (ten sam bukmacher → pomiń)
            if best_1[0] == best_2[0] and not force_show_all:
                continue

            profit = compute_profit_with_tax(best_1[1], best_2[1])
            # jeśli <0 (lub < minimal_profit), to pomiń
            if profit < minimal_profit and not force_show_all:
                continue

            surebets.append({
                "match_id":   mid,
                "match_name": sts["match_name"],
                "datetime":   sts["datetime"].replace("T", " "),
                "sport":      sts["sport"],
                "league":     sts.get("league", ""),
                "submarket":  submkt,
                "profit":     profit,
                "bets": [
                    {"bookmaker": best_1[0], "selection": items[0][0], "odds": best_1[1]},
                    {"bookmaker": best_2[0], "selection": items[1][0], "odds": best_2[1]}
                ]
            })

    return surebets

def format_for_discord(surebet):
    is_lamak = surebet["bets"][0]["bookmaker"] == surebet["bets"][1]["bookmaker"]
    profit   = surebet["profit"]
    profit_str = f"{profit:+.2f}%"

    lines = []
    lines.append("⚠️ ŁAMAK!" if is_lamak else "🟢 WYKRYTO SUREBET!")
    lines.append(f"Profit po podatku (12%): {profit_str}")
    lines.append("")
    liga = surebet.get("league", "")
    match_line = f"{surebet['match_name']}   | {liga}" if liga else surebet['match_name']
    lines.append(f"Mecz i liga: ||{match_line}||")
    lines.append(f"Data: {surebet['datetime']}")
    lines.append(f"Sport: {surebet['sport']}")

    # Wypiszmy submarket w przyjaźniejszej formie:
    subm = surebet["submarket"]
    if subm.startswith("over_under:"):
        line = subm.split(":", 1)[1]
        lines.append(f"Rynek: Over/Under {line}")
    elif subm.startswith("handicap:"):
        line = subm.split(":", 1)[1]
        lines.append(f"Rynek: ||Handicap {line}||")
    else:
        lines.append(f"Rynek: ||{subm}||")

    lines.append("")
    lines.append("Typy do zagrania:")
    for bet in surebet["bets"]:
        lines.append(f"||🏦 {bet['bookmaker']}:   {bet['selection'].upper()} @ {bet['odds']}||")

    return "\n".join(lines)

if __name__ == "__main__":
    sts_data     = load_csv("sts_data.csv")
    fortuna_data = load_csv("fortuna_data.csv")
    results      = compute_surebets(sts_data, fortuna_data)
    for sb in results:
        print(format_for_discord(sb))
        print("\n" + "-" * 60 + "\n")
