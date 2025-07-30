import yaml
from pathlib import Path
import logging

log = logging.getLogger(__name__)

def load_config(config_path="config.yaml"):
    """Loads the YAML configuration file."""
    path = Path(config_path)
    if not path.exists():
        log.error(f"Configuration file not found at: {config_path}")
        raise FileNotFoundError(f"Configuration file not found at: {config_path}")
    
    with open(path, 'r') as f:
        config = yaml.safe_load(f)
    log.info("Configuration file loaded successfully.")
    return config

# Load config once and export it
try:
    config = load_config()
except FileNotFoundError as e:
    # Handle case where config is missing, maybe exit or use defaults
    log.critical(e)
    # For this application, we will exit if config is not found.
    exit(1)