import yaml
import threading

class ConfigManager:
    def __init__(self, config_path='config.yaml'):
        self.config_path = config_path
        self.lock = threading.Lock()  # zabezpieczenie do zapisu
        self.load_config()

    def load_config(self):
        with open(self.config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

    def save_config(self):
        with self.lock:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(self.config, f, allow_unicode=True)

    def get(self, *keys, default=None):
        # Pobiera wartość z configu wg kluczy, np. get('discord', 'channels', 'free')
        cfg = self.config
        try:
            for key in keys:
                cfg = cfg[key]
            return cfg
        except (KeyError, TypeError):
            return default

    def set(self, value, *keys):
        # Ustawia wartość w configu wg kluczy, np. set(123456, 'discord', 'channels', 'free')
        cfg = self.config
        for key in keys[:-1]:
            if key not in cfg or not isinstance(cfg[key], dict):
                cfg[key] = {}
            cfg = cfg[key]
        cfg[keys[-1]] = value
        self.save_config()
