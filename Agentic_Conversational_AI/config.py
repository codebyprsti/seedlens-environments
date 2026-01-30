"""
Configuration file for Conversational Database Analytics Agent
All settings are loaded from environment variables or config file
"""
import os
import yaml
from pathlib import Path


class Config:
    """Configuration class for the application"""
    
    _config_data = None
    
    @classmethod
    def load_config(cls, config_file: str = 'config.yaml'):
        """Load configuration from YAML file"""
        config_path = Path(config_file)
        if config_path.exists():
            with open(config_path, 'r') as f:
                cls._config_data = yaml.safe_load(f)
        else:
            cls._config_data = {}
    
    @classmethod
    def _get(cls, key: str, default=None):
        """Get value from config file or environment variable"""
        if cls._config_data is None:
            cls.load_config()
        
        # Try environment variable first (for overrides)
        env_value = os.getenv(key.upper())
        if env_value is not None:
            return env_value
        
        # Then try config file
        if cls._config_data and key in cls._config_data:
            return cls._config_data[key]
        
        return default
    
    # Flask Server Configuration
    @classmethod
    @property
    def HOST(cls):
        return cls._get('flask_host', '0.0.0.0')
    
    @classmethod
    @property
    def PORT(cls):
        return int(cls._get('flask_port', 5000))
    
    @classmethod
    @property
    def DEBUG(cls):
        debug_val = cls._get('flask_debug', 'false')
        return str(debug_val).lower() == 'true'
    
    # LLM API Configuration
    @classmethod
    @property
    def LLM_API_URL(cls):
        return cls._get('llm_api_url', '')
    
    # Database Configuration
    @classmethod
    @property
    def DB_CONFIG(cls):
        if cls._config_data is None:
            cls.load_config()
        
        # Try to get from config file first
        if cls._config_data and 'database' in cls._config_data:
            db_conf = cls._config_data['database']
            return {
                'host': db_conf.get('host', 'localhost'),
                'port': int(db_conf.get('port', 5432)),
                'database': db_conf.get('name', ''),
                'user': db_conf.get('user', ''),
                'password': db_conf.get('password', '')
            }
        
        # Fallback to environment variables
        db_name = os.getenv('DB_NAME', '')
        db_user = os.getenv('DB_USER', '')
        
        if db_name and db_user:
            return {
                'host': os.getenv('DB_HOST', 'localhost'),
                'port': int(os.getenv('DB_PORT', 5432)),
                'database': db_name,
                'user': db_user,
                'password': os.getenv('DB_PASSWORD', '')
            }
        
        return None
    
    # Table Context Configuration
    @classmethod
    @property
    def DEFAULT_SCHEMA(cls):
        if cls._config_data is None:
            cls.load_config()
        
        if cls._config_data and 'table_context' in cls._config_data:
            return cls._config_data['table_context'].get('schema', 'public')
        
        return os.getenv('DEFAULT_SCHEMA', 'public')
    
    @classmethod
    @property
    def DEFAULT_TABLE(cls):
        if cls._config_data is None:
            cls.load_config()
        
        if cls._config_data and 'table_context' in cls._config_data:
            return cls._config_data['table_context'].get('table', '')
        
        return os.getenv('DEFAULT_TABLE', '')
    
    # Agent Configuration
    @classmethod
    @property
    def SQL_GENERATION_TEMPERATURE(cls):
        return float(cls._get('sql_temperature', 0.1))
    
    @classmethod
    @property
    def MAX_TOKENS(cls):
        return int(cls._get('max_tokens', 500))
    
    @classmethod
    @property
    def MAX_RESULT_ROWS(cls):
        return int(cls._get('max_result_rows', 1000))

    # Advanced LLM Configuration
    @classmethod
    @property
    def LLM_API_KEY(cls):
        return cls._get('llm_api_key', '')
    
    @classmethod
    @property
    def LLM_MODEL(cls):
        return cls._get('llm_model', '')
    
    @classmethod
    @property
    def FEW_SHOT_PROMPT(cls):
        if cls._config_data is None:
            cls.load_config()
        
        if cls._config_data and 'few_shot_prompt' in cls._config_data:
            return cls._config_data['few_shot_prompt']
        
        return os.getenv('FEW_SHOT_PROMPT', '')
    
    @classmethod
    @property
    def SQL_RULES(cls):
        if cls._config_data is None:
            cls.load_config()
        
        if cls._config_data and 'sql_rules' in cls._config_data:
            return cls._config_data['sql_rules']
        
        return os.getenv('SQL_RULES', '')

