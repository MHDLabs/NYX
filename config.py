import json
import logging
from pathlib import Path
from typing import Any, Dict


class ConfigObject:
    """Configuration object with attribute access"""
    
    def __init__(self, data: Dict[str, Any]):
        self._data = data
    
    def __getattr__(self, name: str) -> Any:
        if name.startswith('_'):
            return object.__getattribute__(self, name)
        
        value = self._data.get(name)
        if isinstance(value, dict):
            return ConfigObject(value)
        return value
    
    def __getitem__(self, key: str) -> Any:
        return self._data[key]
    
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class Settings:
    """Settings object with nested access"""
    
    def __init__(self, data: Dict[str, Any]):
        self._data = data
        self.network = ConfigObject(data.get('network', {}))
        self.storage = StorageConfig(data.get('storage', {}))
        self.ui = ConfigObject(data.get('ui', {}))


class StorageConfig(ConfigObject):
    """Storage configuration with database_path method"""
    
    def database_path(self) -> str:
        """Get database path"""
        db_path = self._data.get('database_path', '~/.nyx/nyx.db')
        return str(Path(db_path).expanduser())


def load_settings(config_path: str = None) -> Settings:
    """Load settings from config file"""
    if config_path is None:
        config_path = str(Path.home() / '.nyx' / 'config.json')
    
    config_file = Path(config_path)
    
    # Default settings
    default_settings = {
        'network': {
            'default_server': 'http://localhost:8000'
        },
        'storage': {
            'database_path': '~/.nyx/nyx.db'
        },
        'ui': {
            'theme': 'default',
            'prompt': 'nyx> '
        }
    }
    
    # Load from file if exists
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                user_settings = json.load(f)
                # Merge with defaults
                default_settings.update(user_settings)
        except Exception as e:
            print(f"Warning: Could not load config from {config_path}: {e}")
    else:
        # Create default config file
        try:
            config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(config_file, 'w') as f:
                json.dump(default_settings, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save default config: {e}")
    
    return Settings(default_settings)


def save_settings(settings: Settings, config_path: str = None):
    """Save settings to config file"""
    if config_path is None:
        config_path = str(Path.home() / '.nyx' / 'config.json')
    
    config_file = Path(config_path)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(config_file, 'w') as f:
        json.dump(settings._data, f, indent=2)


def get_logger(name: str):
    """Get a logger instance"""
    return logging.getLogger(name)