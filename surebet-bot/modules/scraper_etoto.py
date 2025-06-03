# modules/scraper_etoto.py

import requests
from modules.proxy_manager import ProxyManager
from modules.config_manager import ConfigManager

# Inicjalizacja Config i ProxyManager
config = ConfigManager('config.yaml')
proxy_mgr = ProxyManager(config)

ETOTO_LIVE_URL = (
    "https://api.etoto.pl/livebetting-api/rest/livebetting/v1/api/running/games/major"
)


def get_surebets():
    """
    Pobiera listę meczów z API Etoto i zwraca oferty Over/Under na różnych liniach.
    Każdy element listy to:
      {
        'match': str,
        'odds': dict {'U<line>': odd_under, 'O<line>': odd_over},
        'bookmakers': ['Etoto'],
        'value': None
      }
    """
    kwargs = proxy_mgr.get_request_kwargs()
    resp = requests.get(ETOTO_LIVE_URL, timeout=10, **kwargs)
    resp.raise_for_status()
    data = resp.json()

    offers = []
    for event in data.get('games', []):
        # Składniki meczu
        parts = event.get('participants', [])
        if len(parts) >= 2:
            home = parts[0].get('participantName')
            away = parts[1].get('participantName')
        else:
            home = event.get('eventName')
            away = None
        match_name = f"{home} vs {away}" if away else home

        # Przeglądaj dostępne rynki (lista games)
        for market in event.get('games', []):
            name = market.get('gameName', '')
            # only Over/Under markets
            if 'Under/' not in name and 'Over' not in name:
                continue

            line = market.get('argument')  # np. 90.5
            outcomes = market.get('outcomes', [])
            if line is None or len(outcomes) < 2:
                continue

            u_odd = None
            o_odd = None
            for o in outcomes:
                if o.get('outcomeName', '').startswith('Under'):
                    u_odd = o.get('outcomeOdds')
                elif o.get('outcomeName', '').startswith('Over'):
                    o_odd = o.get('outcomeOdds')

            if u_odd is None or o_odd is None:
                continue

            offers.append({
                'match':      match_name,
                'odds':       {f"U{line}": u_odd, f"O{line}": o_odd},
                'bookmakers': ['Etoto'],
                'value':      None
            })

    return offers


if __name__ == "__main__":
    from pprint import pprint
    sb = get_surebets()
    pprint(sb)
