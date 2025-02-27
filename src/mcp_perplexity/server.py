import os
from textwrap import dedent
import json
from collections import deque
from datetime import datetime
import uuid
from typing import List, Dict, Optional
import sqlite3
from pathlib import Path
import logging

import mcp.server.stdio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from haikunator import Haikunator

from .perplexity_client import PerplexityClient
from .database import DatabaseManager, Chat, Message
from .utils import get_logs_dir
from .settings import DB_PATH

# Set up logger
logger = logging.getLogger(__name__)

haikunator = Haikunator()

SYSTEM_PROMPT = """You are an expert assistant providing accurate answers to technical questions.
Your responses must:
1. Be based on the most relevant web sources
2. Include source citations for all factual claims
3. If no relevant results are found, suggest 2-3 alternative search queries that might better uncover the needed information
4. Prioritize technical accuracy, especially for programming-related questions"""

server = Server("mcp-server-perplexity")
db_manager = DatabaseManager(DB_PATH)

# Initialize default settings from environment variables
db_manager.initialize_default_settings()

# Get all settings and check if we should override env vars
settings = db_manager.get_all_settings()
override_env_vars = db_manager.should_override_env_vars()

# Create client with settings and override flag
perplexity_client = PerplexityClient(settings=settings, override_env_vars=override_env_vars)

# Update SYSTEM_PROMPT to use settings if overriding env vars
if override_env_vars and 'system_prompt' in settings:
    SYSTEM_PROMPT = settings['system_prompt']

# Function to reload settings and recreate client
def reload_settings():
    global perplexity_client, SYSTEM_PROMPT, settings, override_env_vars
    
    # Get updated settings
    settings = db_manager.get_all_settings()
    override_env_vars = db_manager.should_override_env_vars()
    
    # Recreate client with new settings
    perplexity_client = PerplexityClient(settings=settings, override_env_vars=override_env_vars)
    
    # Update SYSTEM_PROMPT if needed
    if override_env_vars and 'system_prompt' in settings:
        SYSTEM_PROMPT = settings['system_prompt']

def init_db():
    try:
        # Create parent directories if needed
        db_dir = os.path.dirname(DB_PATH)
        if db_dir:  # Only create directories if path contains them
            os.makedirs(db_dir, exist_ok=True)

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Create tables with enhanced error handling
        c.execute('''CREATE TABLE IF NOT EXISTS chats
                     (id TEXT PRIMARY KEY,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      title TEXT)''')

        c.execute('''CREATE TABLE IF NOT EXISTS messages
                     (id TEXT PRIMARY KEY,
                      chat_id TEXT,
                      role TEXT,
                      content TEXT,
                      timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY(chat_id) REFERENCES chats(id))''')

        # Verify table creation
        c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('chats', 'messages')")
        existing_tables = {row[0] for row in c.fetchall()}
        if 'chats' not in existing_tables or 'messages' not in existing_tables:
            raise RuntimeError("Failed to create database tables")

        conn.commit()
    except sqlite3.Error as e:
        raise RuntimeError(f"Database connection error: {str(e)}")
    except Exception as e:
        raise RuntimeError(
            f"Failed to initialize database at '{DB_PATH}': {str(e)}")
    finally:
        if 'conn' in locals():
            conn.close()


# Initialize database on startup
init_db()


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="ask_perplexity",
            description=dedent(
                """
                Provides expert programming assistance through Perplexity.
                This tool only has access to the context you have provided. It cannot read any file unless you provide it with the file content.
                Focuses on coding solutions, error debugging, and technical explanations.
                Returns responses with source citations and alternative suggestions.
                """
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Technical question or problem to solve"
                    }
                },
                "required": ["query"]
            },
        ),
        types.Tool(
            name="chat_perplexity",
            description=dedent("""
                Maintains ongoing conversations with Perplexity AI.
                Creates new chats or continues existing ones with full history context.
                This tool only has access to the context you have provided. It cannot read any file unless you provide it with the file content.
                Returns chat ID for future continuation.

                For new chats: Provide 'message' and 'title'
                For existing chats: Provide 'chat_id' and 'message'
                """),
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "New message to add to the conversation"
                    },
                    "chat_id": {
                        "type": "string",
                        "description": "ID of an existing chat to continue. If not provided, a new chat will be created and title is required."
                    },
                    "title": {
                        "type": "string",
                        "description": "Title for the new chat. Required when creating a new chat (when chat_id is not provided)."
                    }
                },
                "required": ["message"]
            },
        ),
        types.Tool(
            name="list_chats_perplexity",
            description=dedent("""
                Lists all available chat conversations with Perplexity AI.
                Returns chat IDs, titles, and creation dates.
                Results are paginated with 50 chats per page.
                """),
            inputSchema={
                "type": "object",
                "properties": {
                    "page": {
                        "type": "integer",
                        "description": "Page number (defaults to 1)",
                        "minimum": 1
                    }
                }
            },
        ),
        types.Tool(
            name="read_chat_perplexity",
            description=dedent("""
                Retrieves the complete conversation history for a specific chat.
                Returns the full chat history with all messages and their timestamps.
                No API calls are made to Perplexity - this only reads from local storage.
                """),
            inputSchema={
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": "string",
                        "description": "ID of the chat to retrieve"
                    }
                },
                "required": ["chat_id"]
            },
        )
    ]


def generate_chat_id():
    return haikunator.haikunate(token_length=2, delimiter='-').lower()


def store_message(chat_id: str, role: str, content: str, title: Optional[str] = None) -> None:
    with db_manager.get_session() as session:
        # Create chat if it doesn't exist
        chat = session.query(Chat).filter(Chat.id == chat_id).first()
        if not chat:
            chat = Chat(id=chat_id, title=title)
            session.add(chat)
            session.flush()  # Ensure chat is created before adding message

        # Create and store message
        message = Message(
            id=str(uuid.uuid4()),
            chat_id=chat_id,
            role=role,
            content=content
        )
        session.add(message)


def get_chat_history(chat_id: str) -> List[Dict[str, str]]:
    with db_manager.get_session() as session:
        messages = session.query(Message).filter(
            Message.chat_id == chat_id
        ).order_by(Message.timestamp.asc()).all()
        
        # Access attributes within the session context
        result = []
        for msg in messages:
            content = msg.content
            # For assistant messages, we need to keep the content as is
            result.append({"role": msg.role, "content": content})
        
        # Add system message at the beginning if not present
        has_system = any(msg["role"] == "system" for msg in result)
        if not has_system:
            result.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
            
        return result


def get_relative_time(timestamp: datetime) -> str:
    try:
        # Get current time in UTC for comparison
        now_utc = datetime.utcnow()

        # Calculate the time difference
        diff = now_utc - timestamp
        seconds = diff.total_seconds()

        # For future dates or dates too far in the future/past, show the actual date
        if abs(seconds) > 31536000:  # More than a year
            # Convert to local time for display
            local_dt = timestamp + (datetime.now() - datetime.utcnow())
            return local_dt.strftime("%Y-%m-%d %H:%M:%S")

        if seconds < 0:  # Future dates within a year
            seconds = abs(seconds)
            prefix = "in "
            suffix = ""
        else:
            prefix = ""
            suffix = " ago"

        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            return f"{prefix}{minutes} minute{'s' if minutes != 1 else ''}{suffix}"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f"{prefix}{hours} hour{'s' if hours != 1 else ''}{suffix}"
        elif seconds < 604800:  # 7 days
            days = int(seconds / 86400)
            return f"{prefix}{days} day{'s' if days != 1 else ''}{suffix}"
        elif seconds < 2592000:  # 30 days
            weeks = int(seconds / 604800)
            return f"{prefix}{weeks} week{'s' if weeks != 1 else ''}{suffix}"
        else:  # less than a year
            months = int(seconds / 2592000)
            return f"{prefix}{months} month{'s' if months != 1 else ''}{suffix}"
    except Exception:
        return str(timestamp)  # Return original datetime if parsing fails


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    context = server.request_context
    progress_token = context.meta.progressToken if context.meta else None

    if name == "ask_perplexity":
        try:
            # Initialize progress tracking
            initial_estimate = 1000
            progress_counter = 0
            total_estimate = initial_estimate
            chunk_sizes = deque(maxlen=10)  # Store last 10 chunk sizes
            chunk_count = 0

            if progress_token:
                await context.session.send_progress_notification(
                    progress_token=progress_token,
                    progress=0,
                    total=initial_estimate,
                )

            # Get profile for ask tool if available
            profile_settings = {}
            try:
                from .web.database_extension import db
                profile = db.get_profile_for_tool('ask')
                if profile:
                    profile_settings = {
                        'model': profile['model'],
                        'temperature': profile['temperature'],
                        'top_p': profile['top_p']
                    }
                    if profile['system_prompt']:
                        profile_settings['system_prompt'] = profile['system_prompt']
                    logger.info(f"Using profile for ask: {profile['name']}")
            except Exception as e:
                logger.error(f"Error getting profile for ask: {e}")

            full_response = ""
            citations = []
            usage = {}

            async for content, current_citations, current_usage in perplexity_client.ask(
                arguments["query"], 
                profile_settings=profile_settings
            ):
                full_response += content
                citations = current_citations
                usage = current_usage

                # Update progress tracking
                tokens_in_chunk = len(content.split())
                progress_counter += tokens_in_chunk
                chunk_count += 1
                chunk_sizes.append(tokens_in_chunk)

                # Update total estimate every 5 chunks
                if chunk_count % 5 == 0 and chunk_sizes:
                    avg_chunk_size = sum(chunk_sizes) / len(chunk_sizes)
                    total_estimate = max(initial_estimate,
                                         int(progress_counter + avg_chunk_size * 10))

                if progress_token:
                    await context.session.send_progress_notification(
                        progress_token=progress_token,
                        progress=progress_counter,
                        total=total_estimate,
                    )

            citation_list = "\n".join(
                f"{i}. {url}" for i, url in enumerate(citations, start=1))
            
            # Handle empty citations
            if not citation_list:
                citation_list = "No sources available for this response."
                
            # Format the response text for display
            response_text = (
                f"{full_response}\n\n"
                f"Sources:\n{citation_list}\n\n"
                f"API Usage:\n"
                f"- Prompt tokens: {usage.get('prompt_tokens', 'N/A')}\n"
                f"- Completion tokens: {usage.get('completion_tokens', 'N/A')}\n"
                f"- Total tokens: {usage.get('total_tokens', 'N/A')}"
            )

            if progress_token:
                await context.session.send_progress_notification(
                    progress_token=progress_token,
                    progress=progress_counter,
                    total=progress_counter  # Set final total to actual tokens received
                )

            return [
                types.TextContent(
                    type="text",
                    text=response_text
                )
            ]

        except Exception as e:
            if progress_token:
                await context.session.send_progress_notification(
                    progress_token=progress_token,
                    progress=progress_counter if 'progress_counter' in locals() else 0,
                    total=progress_counter if 'progress_counter' in locals() else 0,
                )
            raise RuntimeError(f"API error: {str(e)}")

    elif name == "chat_perplexity":
        chat_id = arguments.get("chat_id") or generate_chat_id()
        user_message = arguments["message"]
        title = arguments.get("title")

        # Store user message
        if not arguments.get("chat_id"):  # Only store title for new chats
            store_message(chat_id, "user", user_message, title or "Untitled")
        else:
            store_message(chat_id, "user", user_message)

        # Get full chat history
        chat_history = get_chat_history(chat_id)

        try:
            # Initialize progress tracking
            initial_estimate = 1000
            progress_counter = 0
            total_estimate = initial_estimate
            chunk_sizes = deque(maxlen=10)
            chunk_count = 0

            if progress_token:
                await context.session.send_progress_notification(
                    progress_token=progress_token,
                    progress=0,
                    total=initial_estimate,
                )

            # Get profile for chat tool if available
            profile_settings = {}
            try:
                from .web.database_extension import db
                profile = db.get_profile_for_tool('chat')
                if profile:
                    profile_settings = {
                        'model': profile['model'],
                        'temperature': profile['temperature'],
                        'top_p': profile['top_p']
                    }
                    if profile['system_prompt']:
                        profile_settings['system_prompt'] = profile['system_prompt']
                    logger.info(f"Using profile for chat: {profile['name']}")
            except Exception as e:
                logger.error(f"Error getting profile for chat: {e}")

            full_response = ""
            citations = []
            usage = {}

            async for content, current_citations, current_usage in perplexity_client.chat(
                chat_history,
                profile_settings=profile_settings
            ):
                full_response += content
                citations = current_citations
                usage = current_usage

                # Update progress tracking
                tokens_in_chunk = len(content.split())
                progress_counter += tokens_in_chunk
                chunk_count += 1
                chunk_sizes.append(tokens_in_chunk)

                # Update total estimate every 5 chunks
                if chunk_count % 5 == 0 and chunk_sizes:
                    avg_chunk_size = sum(chunk_sizes) / len(chunk_sizes)
                    total_estimate = max(initial_estimate,
                                         int(progress_counter + avg_chunk_size * 10))

                if progress_token:
                    await context.session.send_progress_notification(
                        progress_token=progress_token,
                        progress=progress_counter,
                        total=total_estimate,
                    )

            citation_list = "\n".join(
                f"{i}. {url}" for i, url in enumerate(citations, start=1))
            
            # Handle empty citations
            if not citation_list:
                citation_list = "No sources available for this response."

            # Format full response with sources for storage
            response_with_sources = (
                f"{full_response}\n\n"
                f"Sources:\n{citation_list}"
            )

            # Store assistant response
            store_message(chat_id, "assistant", response_with_sources)

            # Format chat history
            history_text = "\nChat History:\n"
            for msg in chat_history:
                role = "You" if msg["role"] == "user" else "Assistant"
                history_text += f"\n{role}: {msg['content']}\n"

            response_text = (
                f"Chat ID: {chat_id}\n"
                f"{history_text}\n"
                f"Current Response:\n{full_response}\n\n"
                f"Sources:\n{citation_list}"
            )

            if progress_token:
                await context.session.send_progress_notification(
                    progress_token=progress_token,
                    progress=progress_counter,
                    total=progress_counter,  # Set final total to actual tokens received
                )

            return [
                types.TextContent(
                    type="text",
                    text=response_text
                )
            ]

        except Exception as e:
            if progress_token:
                await context.session.send_progress_notification(
                    progress_token=progress_token,
                    progress=progress_counter if 'progress_counter' in locals() else 0,
                    total=progress_counter if 'progress_counter' in locals() else 0,
                )
            raise RuntimeError(f"API error: {str(e)}")

    elif name == "list_chats_perplexity":
        page = arguments.get("page", 1)
        page_size = 50
        offset = (page - 1) * page_size

        with db_manager.get_session() as session:
            # Get total count for pagination info
            total_chats = session.query(Chat).count()
            total_pages = (total_chats + page_size - 1) // page_size

            # Get paginated chats with message count
            chats = session.query(Chat).order_by(
                Chat.created_at.desc()).offset(offset).limit(page_size).all()

            # Format the response
            header = (
                f"Page {page} of {total_pages}\n"
                f"Total chats: {total_chats}\n\n"
                f"{'=' * 40}\n"
            )

            chat_list = []
            for chat in chats:
                message_count = len(chat.messages)
                relative_time = get_relative_time(chat.created_at)
                title = chat.title if chat.title is not None else 'Untitled'
                chat_list.append(
                    f"Chat ID: {chat.id}\n"
                    f"Title: {title}\n"
                    f"Created: {relative_time}\n"
                    f"Messages: {message_count}"
                )

            response_text = header + "\n\n".join(chat_list)

            return [types.TextContent(type="text", text=response_text)]

    elif name == "read_chat_perplexity":
        chat_id = arguments["chat_id"]

        with db_manager.get_session() as session:
            chat = session.query(Chat).filter(Chat.id == chat_id).first()
            if not chat:
                raise ValueError(f"Chat with ID {chat_id} not found")

            messages = session.query(Message).filter(
                Message.chat_id == chat_id
            ).order_by(Message.timestamp.asc()).all()

            # Format the response
            chat_header = (
                f"Chat ID: {chat.id}\n"
                f"Title: {chat.title if chat.title is not None else 'Untitled'}\n"
                f"Created: {chat.created_at}\n"
                f"Messages: {len(messages)}\n\n"
                f"{'=' * 40}\n\n"
            )

            message_history = []
            for message in messages:
                role_display = "You" if message.role == "user" else "Assistant"
                message_history.append(
                    f"[{message.timestamp}] {role_display}:\n{message.content}\n"
                )

        response_text = chat_header + "\n".join(message_history)

        return [types.TextContent(type="text", text=response_text)]

    else:
        raise ValueError(f"Unknown tool: {name}")


async def main():
    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="mcp-server-perplexity",
                    server_version="0.1.2",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(
                            tools_changed=True),
                        experimental_capabilities={},
                    ),
                ),
            )
    except Exception as e:
        print(f"Server error: {str(e)}", flush=True)
        raise
    print("Server shutdown", flush=True)
