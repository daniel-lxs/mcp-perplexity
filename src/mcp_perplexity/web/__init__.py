import os
import logging
from pathlib import Path
from quart import Quart
from markdown2 import markdown
from ..database import DatabaseManager
from .database_extension import db
from .. import get_logs_dir
from ..settings import DEBUG_LOGS, WEB_UI_ENABLED, WEB_UI_PORT, WEB_UI_HOST

# Setup logging
logger = logging.getLogger(__name__)

# Only create logs directory and set up file handlers if DEBUG_LOGS is enabled
if DEBUG_LOGS:
    # Ensure logs directory exists
    logs_dir = get_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)

    # File handler for web operations
    web_handler = logging.FileHandler(str(logs_dir / "web.log"))
    web_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(web_handler)

    # File handler for template debugging
    template_handler = logging.FileHandler(str(logs_dir / "templates.log"))
    template_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    
    # Set log level for debug mode
    logger.setLevel(logging.INFO)
    template_handler.setLevel(logging.INFO)
    
    # Add template handler to the template logger
    template_logger = logging.getLogger('template_debug')
    template_logger.addHandler(template_handler)
    template_logger.propagate = False
else:
    # Set critical log level when debug logs are disabled
    logger.setLevel(logging.CRITICAL)
    template_logger = logging.getLogger('template_debug')
    template_logger.setLevel(logging.CRITICAL)

# Disable propagation to prevent stdout logging
logger.propagate = False

def create_app():
    if not WEB_UI_ENABLED:
        logger.info("Web UI is disabled via environment variables")
        return None

    try:
        app = Quart(__name__)

        # Configure template and static directories
        app.template_folder = str(Path(__file__).parent / 'templates')
        app.static_folder = str(Path(__file__).parent / 'static')

        # Add markdown filter
        def custom_markdown_filter(text):
            # Handle <think> tags preservation and transformation to collapsible elements
            import re
            import html
            
            # First, let's log the original text for debugging
            template_logger.info(f"Original text before processing: {text[:100]}...")
            
            # Extract and save <think> blocks
            think_pattern = re.compile(r'<think>(.*?)</think>', re.DOTALL)
            think_matches = think_pattern.findall(text)
            
            # If no think blocks found, just process markdown normally
            if not think_matches:
                return markdown(text, extras=['fenced-code-blocks', 'tables'])
            
            # Replace each <think> block with a unique placeholder
            # Use a format that's unlikely to be affected by markdown processing
            for i, content in enumerate(think_matches):
                placeholder = f"THINKBLOCK{i}PLACEHOLDER"
                text = text.replace(f"<think>{content}</think>", placeholder)
            
            # Process markdown
            html_content = markdown(text, extras=['fenced-code-blocks', 'tables'])
            template_logger.info(f"After markdown processing: {html_content[:100]}...")
            
            # Restore <think> blocks as collapsible details elements
            for i, content in enumerate(think_matches):
                placeholder = f"THINKBLOCK{i}PLACEHOLDER"
                # Process the content with markdown
                processed_content = markdown(content, extras=['fenced-code-blocks', 'tables'])
                
                # Create a collapsible details element
                details_element = (
                    f'<details class="think">'
                    f'<summary>Thought process</summary>'
                    f'<div class="think-content">{processed_content}</div>'
                    f'</details>'
                )
                
                # Try different possible formats the placeholder might have
                if placeholder in html_content:
                    html_content = html_content.replace(placeholder, details_element)
                elif f"<p>{placeholder}</p>" in html_content:
                    html_content = html_content.replace(f"<p>{placeholder}</p>", details_element)
                else:
                    # If we can't find the exact placeholder, try a more aggressive approach
                    template_logger.info(f"Placeholder {placeholder} not found in exact form, trying regex")
                    pattern = re.compile(fr'{placeholder}|<p>\s*{placeholder}\s*</p>', re.IGNORECASE)
                    html_content = pattern.sub(details_element, html_content)
            
            template_logger.info(f"Final HTML after restoring think blocks: {html_content[:100]}...")
            return html_content
            
        app.jinja_env.filters['markdown'] = custom_markdown_filter

        # Initialize database extension
        db.init_app(app)

        # Register routes
        from .routes import register_routes
        register_routes(app)

        logger.info(
            f"Web UI initialized successfully on {WEB_UI_HOST}:{WEB_UI_PORT}")
        return app
    except Exception as e:
        logger.error(f"Failed to initialize web UI: {e}")
        return None
