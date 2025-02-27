from typing import Optional, List, Dict, Any
from quart import Quart
from ..database import DatabaseManager, Chat, Message, Setting
from datetime import datetime
import importlib
import logging
import os
from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, DateTime
from sqlalchemy.ext.declarative import declarative_base
from ..utils import get_logs_dir
from ..settings import (
    DEBUG_LOGS,
    DB_PATH,
    MODEL_PROFILES,
    DEFAULT_MODEL,
    DEFAULT_SETTINGS,
    DEFAULT_SYSTEM_PROMPT
)
from sqlalchemy.sql import func
from sqlalchemy import inspect
from sqlalchemy.sql import text
import uuid

# Setup logging
logger = logging.getLogger(__name__)
log_dir = get_logs_dir()
log_file = os.path.join(log_dir, 'database.log')

# Ensure log directory exists
os.makedirs(log_dir, exist_ok=True)

# Setup file handler
file_handler = logging.FileHandler(log_file)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)

# Set log level based on environment variable
logger.setLevel(logging.INFO if DEBUG_LOGS else logging.ERROR)

Base = declarative_base()

class Profile(Base):
    __tablename__ = 'profiles'
    
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    model = Column(String, nullable=False)
    temperature = Column(Float, nullable=False)
    top_p = Column(Float, nullable=False)
    system_prompt = Column(String)
    is_default = Column(Boolean, default=False)
    tool = Column(String)  # 'ask' or 'chat'
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'model': self.model,
            'temperature': self.temperature,
            'top_p': self.top_p,
            'system_prompt': self.system_prompt,
            'is_default': self.is_default,
            'tool': self.tool,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    @classmethod
    def create_default_profiles(cls):
        """Create default profiles for ask and chat tools."""
        try:
            # Default profile for ask tool
            ask_profile = cls.query.filter_by(tool='ask', is_default=True).first()
            if not ask_profile:
                ask_profile = cls(
                    id='default_ask',
                    name='Default Ask Profile',
                    model=DEFAULT_MODEL,
                    temperature=MODEL_PROFILES[DEFAULT_MODEL]['temperature'],
                    top_p=MODEL_PROFILES[DEFAULT_MODEL]['top_p'],
                    system_prompt=DEFAULT_SYSTEM_PROMPT,
                    is_default=True,
                    tool='ask'
                )
                session.add(ask_profile)

            # Default profile for chat tool
            chat_profile = cls.query.filter_by(tool='chat', is_default=True).first()
            if not chat_profile:
                chat_profile = cls(
                    id='default_chat',
                    name='Default Chat Profile',
                    model=DEFAULT_MODEL,
                    temperature=MODEL_PROFILES[DEFAULT_MODEL]['temperature'],
                    top_p=MODEL_PROFILES[DEFAULT_MODEL]['top_p'],
                    system_prompt=DEFAULT_SYSTEM_PROMPT,
                    is_default=True,
                    tool='chat'
                )
                session.add(chat_profile)

            session.commit()
            logger.info("Default profiles created successfully")
        except Exception as e:
            session.rollback()
            logger.error(f"Error creating default profiles: {e}")
            raise

class ToolProfile(Base):
    __tablename__ = 'tool_profiles'
    
    tool_name = Column(String, primary_key=True)
    profile_id = Column(Integer, ForeignKey('profiles.id'))

class Database:
    def __init__(self, app: Optional[Quart] = None):
        self._database_manager: Optional[DatabaseManager] = None
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Quart):
        self._database_manager = DatabaseManager()
        
        # Create tables and run migrations
        self._run_migrations()
        
        # Initialize default profiles
        self.initialize_default_profiles()
        
        # Initialize tool profiles if not set
        self.initialize_tool_profiles()

        # Register extension with Quart
        if not hasattr(app, 'extensions'):
            app.extensions = {}
        app.extensions['database'] = self

    def _run_migrations(self):
        """Run database migrations to update schema."""
        try:
            with self.database_manager.get_session() as session:
                # Create tables if they don't exist
                Base.metadata.create_all(self.database_manager.engine)
                
                # Check if profiles table exists and has required columns
                inspector = inspect(self.database_manager.engine)
                if 'profiles' in inspector.get_table_names():
                    columns = {col['name'] for col in inspector.get_columns('profiles')}
                    
                    # Add tool column if it doesn't exist
                    if 'tool' not in columns:
                        session.execute(text('ALTER TABLE profiles ADD COLUMN tool VARCHAR'))
                    
                    # Add timestamp columns if they don't exist
                    if 'created_at' not in columns:
                        # Add column without default first
                        session.execute(text('ALTER TABLE profiles ADD COLUMN created_at TIMESTAMP'))
                        # Then update existing rows with current timestamp
                        session.execute(text('UPDATE profiles SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL'))
                    
                    if 'updated_at' not in columns:
                        session.execute(text('ALTER TABLE profiles ADD COLUMN updated_at TIMESTAMP'))
                
                # Create tool_profiles table if it doesn't exist
                if 'tool_profiles' not in inspector.get_table_names():
                    ToolProfile.__table__.create(self.database_manager.engine)
                
                session.commit()
                logger.info("Database migrations completed successfully")
        except Exception as e:
            logger.error(f"Error running database migrations: {e}")
            raise

    @property
    def database_manager(self) -> DatabaseManager:
        if self._database_manager is None:
            raise RuntimeError(
                "Database extension not initialized. "
                "Did you forget to call init_app()?")
        return self._database_manager

    def get_all_chats(self, page: int = 1, per_page: int = 50) -> Dict:
        """Get all chats with pagination and convert them to dictionaries to avoid session issues.

        Args:
            page: The page number (1-based)
            per_page: Number of items per page (default 50)

        Returns:
            Dict containing chats and pagination info
        """
        with self.database_manager.get_session() as session:
            # Calculate offset
            offset = (page - 1) * per_page

            # Get total count
            total = session.query(Chat).count()

            # Get paginated chats
            chats = session.query(Chat).order_by(Chat.created_at.desc())\
                .offset(offset).limit(per_page).all()

            # Convert to dictionaries while session is still open
            return {
                'chats': [{
                    'id': chat.id,
                    'title': chat.title,
                    'created_at': chat.created_at
                } for chat in chats],
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total,
                    'pages': (total + per_page - 1) // per_page
                }
            }

    def get_chat(self, chat_id: str) -> Optional[Dict]:
        """Get a chat and convert it to a dictionary to avoid session issues."""
        with self.database_manager.get_session() as session:
            chat = session.query(Chat).filter(Chat.id == chat_id).first()
            if not chat:
                return None
            # Convert to dictionary while session is still open
            return {
                'id': chat.id,
                'title': chat.title,
                'created_at': chat.created_at
            }

    def get_chat_messages(self, chat_id: str) -> List[Dict]:
        """Get chat messages and convert them to dictionaries to avoid session issues."""
        with self.database_manager.get_session() as session:
            messages = session.query(Message).filter(
                Message.chat_id == chat_id
            ).order_by(Message.timestamp.asc()).all()
            # Convert to dictionaries while session is still open
            return [{
                'id': msg.id,
                'role': msg.role,
                'content': msg.content,
                'timestamp': msg.timestamp
            } for msg in messages]

    def delete_chat(self, chat_id: str) -> None:
        """Delete a chat and all its messages."""
        with self.database_manager.get_session() as session:
            chat = session.query(Chat).filter(Chat.id == chat_id).first()
            if chat:
                session.delete(chat)
                session.commit()

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self.database_manager.get_setting(key, default)

    def set_setting(self, key: str, value: str) -> None:
        """Set a setting and reload server settings."""
        self.database_manager.set_setting(key, value)
        # Reload settings in the server
        self._reload_server_settings()

    def get_all_settings(self) -> Dict[str, str]:
        return self.database_manager.get_all_settings()

    def should_override_env_vars(self) -> bool:
        """Check if environment variables should be overridden by database settings."""
        return self.database_manager.should_override_env_vars()

    def initialize_default_settings(self) -> None:
        """Initialize default settings from environment variables if they don't exist."""
        self.database_manager.initialize_default_settings()

    def toggle_env_override(self, enable: bool) -> None:
        """Enable or disable environment variable overrides."""
        self.database_manager.set_setting('override_env_vars', str(enable).lower())
        # Reload settings in the server
        self._reload_server_settings()

    def reset_settings_to_defaults(self) -> None:
        """Reset all settings to their default values."""
        # First, get current override setting
        current_override = self.should_override_env_vars()
        
        # Delete all settings (by getting all and then deleting each)
        with self.database_manager.get_session() as session:
            settings = session.query(Setting).all()
            for setting in settings:
                session.delete(setting)
            session.commit()
        
        # Re-initialize defaults
        self.initialize_default_settings()
        
        # Restore override setting if it was enabled
        if current_override:
            self.toggle_env_override(True)
        else:
            # Still need to reload settings even if override is disabled
            self._reload_server_settings()
            
    def _reload_server_settings(self):
        """Reload settings in the server without circular imports."""
        # Import server module dynamically to avoid circular imports
        from .. import server
        # Call the reload_settings function
        server.reload_settings()

    def get_all_profiles(self) -> List[Dict]:
        """Get all profiles from the database"""
        try:
            with self.database_manager.get_session() as session:
                # Get default profiles first, then custom profiles
                profiles = session.query(Profile).order_by(
                    Profile.is_default.desc(),  # Default profiles first
                    Profile.name               # Then alphabetically by name
                ).all()
                
                return [{
                    'id': profile.id,
                    'name': profile.name,
                    'model': profile.model,
                    'temperature': profile.temperature,
                    'top_p': profile.top_p,
                    'system_prompt': profile.system_prompt,
                    'is_default': profile.is_default
                } for profile in profiles]
        except Exception as e:
            logger.error(f"Error getting profiles: {e}")
            return []

    def get_default_profiles(self) -> List[Dict]:
        """Get only default profiles"""
        try:
            with self.database_manager.get_session() as session:
                profiles = session.query(Profile).filter(
                    Profile.is_default == True
                ).order_by(Profile.name).all()
                
                return [{
                    'id': profile.id,
                    'name': profile.name,
                    'model': profile.model,
                    'temperature': profile.temperature,
                    'top_p': profile.top_p,
                    'system_prompt': profile.system_prompt,
                    'is_default': profile.is_default
                } for profile in profiles]
        except Exception as e:
            logger.error(f"Error getting default profiles: {e}")
            return []

    def get_custom_profiles(self) -> List[Dict]:
        """Get only custom (non-default) profiles"""
        try:
            with self.database_manager.get_session() as session:
                profiles = session.query(Profile).filter(
                    Profile.is_default == False
                ).order_by(Profile.name).all()
                
                return [{
                    'id': profile.id,
                    'name': profile.name,
                    'model': profile.model,
                    'temperature': profile.temperature,
                    'top_p': profile.top_p,
                    'system_prompt': profile.system_prompt,
                    'is_default': profile.is_default
                } for profile in profiles]
        except Exception as e:
            logger.error(f"Error getting custom profiles: {e}")
            return []

    def get_profile_by_model(self, model_name: str) -> Optional[Dict]:
        """Get the default profile for a specific model"""
        try:
            with self.database_manager.get_session() as session:
                profile = session.query(Profile).filter(
                    Profile.model == model_name,
                    Profile.is_default == True
                ).first()
                
                if profile:
                    return {
                        'id': profile.id,
                        'name': profile.name,
                        'model': profile.model,
                        'temperature': profile.temperature,
                        'top_p': profile.top_p,
                        'system_prompt': profile.system_prompt,
                        'is_default': profile.is_default
                    }
                return None
        except Exception as e:
            logger.error(f"Error getting profile by model: {e}")
            return None

    def get_tool_profiles(self) -> Dict[str, Optional[Dict]]:
        """Get profiles assigned to each tool"""
        result = {
            'ask': None,
            'chat': None
        }
        
        try:
            with self.database_manager.get_session() as session:
                for tool_name in result.keys():
                    tool_profile = session.query(ToolProfile).filter(
                        ToolProfile.tool_name == tool_name
                    ).first()
                    
                    if tool_profile:
                        profile = session.query(Profile).filter(
                            Profile.id == tool_profile.profile_id
                        ).first()
                        
                        if profile:
                            result[tool_name] = {
                                'id': profile.id,
                                'name': profile.name,
                                'model': profile.model,
                                'temperature': profile.temperature,
                                'top_p': profile.top_p,
                                'system_prompt': profile.system_prompt,
                                'is_default': profile.is_default
                            }
            
            return result
        except Exception as e:
            logger.error(f"Error getting tool profiles: {e}")
            return result

    def initialize_tool_profiles(self) -> bool:
        """Initialize tool profiles based on environment variables if not set"""
        try:
            with self.database_manager.get_session() as session:
                # Check if tool profiles exist
                existing_tools = [tp.tool_name for tp in session.query(ToolProfile).all()]
                
                # Get settings for model names
                settings = self.get_all_settings()
                ask_model = settings.get('ask_model', settings.get('model', ''))
                chat_model = settings.get('chat_model', settings.get('model', ''))
                
                # Set up tool profiles if they don't exist
                if 'ask' not in existing_tools and ask_model:
                    # Find default profile for this model
                    profile = session.query(Profile).filter(
                        Profile.model == ask_model,
                        Profile.is_default == True
                    ).first()
                    
                    if profile:
                        tool_profile = ToolProfile(tool_name='ask', profile_id=profile.id)
                        session.add(tool_profile)
                
                if 'chat' not in existing_tools and chat_model:
                    # Find default profile for this model
                    profile = session.query(Profile).filter(
                        Profile.model == chat_model,
                        Profile.is_default == True
                    ).first()
                    
                    if profile:
                        tool_profile = ToolProfile(tool_name='chat', profile_id=profile.id)
                        session.add(tool_profile)
                
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Error initializing tool profiles: {e}")
            return False

    def initialize_default_profiles(self) -> bool:
        """Initialize default profiles based on MODEL_PROFILES"""
        try:
            with self.database_manager.get_session() as session:
                # Check if default profiles exist
                existing_default_models = [
                    p.model for p in session.query(Profile).filter(Profile.is_default == True).all()
                ]
                
                # Create default profiles for models that don't have one
                for model, settings in MODEL_PROFILES.items():
                    if model not in existing_default_models:
                        # Create a nice display name from the model name
                        name = model.replace('-', ' ').title()
                        
                        profile = Profile(
                            id=f"default_{model}",  # Unique ID for default profiles
                            name=name,
                            model=model,
                            temperature=settings['temperature'],
                            top_p=settings['top_p'],
                            system_prompt=DEFAULT_SYSTEM_PROMPT,
                            is_default=True
                        )
                        session.add(profile)
                
                session.commit()
            return True
        except Exception as e:
            logger.error(f"Error initializing default profiles: {e}")
            return False

    @classmethod
    def get_profile_for_tool(cls, tool: str) -> Optional[Dict[str, Any]]:
        """Get the default profile for a specific tool."""
        try:
            profile = cls.query.filter_by(tool=tool, is_default=True).first()
            return profile.to_dict() if profile else None
        except Exception as e:
            logger.error(f"Error getting profile for tool {tool}: {e}")
            return None

    def get_profile(self, profile_id: int) -> Optional[Dict]:
        """Get a specific profile by ID"""
        try:
            with self.database_manager.get_session() as session:
                profile = session.query(Profile).filter(Profile.id == profile_id).first()
                if profile:
                    return {
                        'id': profile.id,
                        'name': profile.name,
                        'model': profile.model,
                        'temperature': profile.temperature,
                        'top_p': profile.top_p,
                        'system_prompt': profile.system_prompt,
                        'is_default': profile.is_default
                    }
                return None
        except Exception as e:
            logger.error(f"Error getting profile: {e}")
            return None

    def create_profile(self, name: str, model: str, temperature: float, top_p: float, system_prompt: str = "") -> Optional[int]:
        """Create a new profile"""
        try:
            with self.database_manager.get_session() as session:
                # Generate a unique ID based on timestamp and name
                profile_id = f"profile_{str(uuid.uuid4())[:8]}"
                
                profile = Profile(
                    id=profile_id,
                    name=name,
                    model=model,
                    temperature=temperature,
                    top_p=top_p,
                    system_prompt=system_prompt,
                    is_default=False
                )
                session.add(profile)
                session.commit()
                return profile_id
        except Exception as e:
            logger.error(f"Error creating profile: {e}")
            return None

    def update_profile(self, profile_id: int, name: str, model: str, temperature: float, top_p: float, system_prompt: str) -> bool:
        """Update an existing profile"""
        try:
            with self.database_manager.get_session() as session:
                profile = session.query(Profile).filter(
                    Profile.id == profile_id,
                    Profile.is_default == False
                ).first()
                
                if profile:
                    profile.name = name
                    profile.model = model
                    profile.temperature = temperature
                    profile.top_p = top_p
                    profile.system_prompt = system_prompt
                    session.commit()
                    return True
                return False
        except Exception as e:
            logger.error(f"Error updating profile: {e}")
            return False

    def delete_profile(self, profile_id: int) -> bool:
        """Delete a profile"""
        try:
            with self.database_manager.get_session() as session:
                profile = session.query(Profile).filter(Profile.id == profile_id).first()
                if profile and not profile.is_default:
                    # Remove any tool associations
                    session.query(ToolProfile).filter(ToolProfile.profile_id == profile_id).delete()
                    session.delete(profile)
                    session.commit()
                    return True
                return False
        except Exception as e:
            logger.error(f"Error deleting profile: {e}")
            return False

    def get_tool_profile(self, tool_name: str) -> Optional[int]:
        """Get the profile ID assigned to a specific tool"""
        try:
            with self.database_manager.get_session() as session:
                tool_profile = session.query(ToolProfile).filter(ToolProfile.tool_name == tool_name).first()
                return tool_profile.profile_id if tool_profile else None
        except Exception as e:
            logger.error(f"Error getting tool profile: {e}")
            return None

    def set_tool_profile(self, tool_name: str, profile_id: Optional[int]) -> bool:
        """Set the profile for a specific tool"""
        try:
            with self.database_manager.get_session() as session:
                # Delete existing mapping if profile_id is None
                if profile_id is None:
                    session.query(ToolProfile).filter(ToolProfile.tool_name == tool_name).delete()
                    session.commit()
                    return True
                
                # Update or create mapping
                tool_profile = session.query(ToolProfile).filter(ToolProfile.tool_name == tool_name).first()
                if tool_profile:
                    tool_profile.profile_id = profile_id
                else:
                    tool_profile = ToolProfile(tool_name=tool_name, profile_id=profile_id)
                    session.add(tool_profile)
                session.commit()
                return True
        except Exception as e:
            logger.error(f"Error setting tool profile: {e}")
            return False


# Create the extension instance
db = Database()
