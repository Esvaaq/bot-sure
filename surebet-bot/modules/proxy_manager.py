# modules/proxy_manager.py

import random
from modules.config_manager import ConfigManager

class ProxyManager:
    DEFAULT_POLISH_USER_AGENTS = [
        # Przykładowe UA z polskimi lokalizacjami/przeznaczone na PL:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:117.0) Gecko/20100101 Firefox/117.0 (pl)",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.5790.170 Safari/537.36 Edge/115.0.1901.203 (pl-PL)",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15 (pl-PL)",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36 (pl)",
        "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.5672.126 Safari/537.36 (pl-PL)"
    ]

    def __init__(self, config: ConfigManager):
        self.config = config
        # wczytaj listę z configu lub pustą
        self.proxies = self.config.get('scraping', 'proxies', default=[])
        self.user_agents = self.config.get('scraping', 'user_agents', default=[])

    def get_request_kwargs(self) -> dict:
        """
        Zwraca kwargs do requests.get:
          - zawsze losowy User-Agent (polski domyślnie, lub z configu)
          - proxy tylko gdy lista nie jest pusta
        """
        # Wybieraj z configu, a jeśli pusta – z domyślnej listy polskich UA
        if self.user_agents:
            ua = random.choice(self.user_agents)
        else:
            ua = random.choice(self.DEFAULT_POLISH_USER_AGENTS)

        headers = {'User-Agent': ua}

        kwargs = {'headers': headers}
        if self.proxies:
            p = random.choice(self.proxies)
            kwargs['proxies'] = {'http': p, 'https': p}

        return kwargs
