import asyncio
import os
import sys
import logging
from typing import Optional
import socket
from pathlib import Path

from .utils import get_logs_dir
from .server import main as server_main
from .settings import WEB_UI_ENABLED, WEB_UI_PORT, WEB_UI_HOST, DEBUG_LOGS
from .web import create_app

__version__ = "0.5.8"

web_ui_running = False


async def is_port_in_use(host: str, port: int) -> bool:
    """Checks if a port is in use."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)  # Short timeout to avoid hanging
            s.bind((host, port))
            return False  # Port is free
    except OSError:
        return True  # Port is in use


async def run_web_ui():
    global web_ui_running
    if web_ui_running:
        print("Web UI is already running. Skipping.")
        return

    app = create_app()
    if app:
        web_ui_running = True
        try:
            from hypercorn.asyncio import serve
            from hypercorn.config import Config

            config = Config()
            config.bind = [f"{WEB_UI_HOST}:{WEB_UI_PORT}"]

            # Only configure logging if DEBUG_LOGS is enabled
            if DEBUG_LOGS:
                # Set up file logging for Hypercorn
                logs_dir = get_logs_dir()
                logs_dir.mkdir(parents=True, exist_ok=True)
                
                hypercorn_logger = logging.getLogger('hypercorn')
                hypercorn_logger.handlers = []  # Remove any existing handlers
                handler = logging.FileHandler(str(logs_dir / "hypercorn.log"))
                handler.setFormatter(logging.Formatter(
                    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
                hypercorn_logger.addHandler(handler)
                hypercorn_logger.propagate = False

                # Enable logging in config
                config.accesslog = hypercorn_logger
                config.errorlog = hypercorn_logger
                hypercorn_logger.setLevel(logging.INFO)
            else:
                # Completely disable all Hypercorn logging
                config.accesslog = None
                config.errorlog = None

            await serve(app, config)
        except Exception as e:
            # Only log errors if DEBUG_LOGS is enabled
            if DEBUG_LOGS:
                logs_dir = get_logs_dir()
                logs_dir.mkdir(parents=True, exist_ok=True)
                with open(logs_dir / "web_error.log", 'a') as f:
                    f.write(f"Failed to start web UI: {e}\n")
        finally:
            web_ui_running = False


async def run_server(args: Optional[list] = None):
    if args is None:
        args = sys.argv[1:]

    # Create logs directory only if debug logs are enabled
    if DEBUG_LOGS:
        logs_dir = get_logs_dir()
        logs_dir.mkdir(parents=True, exist_ok=True)

    # Start both the web UI and MCP server
    tasks = []

    # Add web UI task if enabled
    if WEB_UI_ENABLED:
        tasks.append(run_web_ui())

    # Add MCP server task - this is the only one that should use stdio
    tasks.append(server_main())

    # Run both tasks concurrently
    await asyncio.gather(*tasks)


def main(args: Optional[list] = None):
    """Main entry point for the package."""
    try:
        # Configure logging based on DEBUG_LOGS environment variable
        if not DEBUG_LOGS:
            # Disable all root logging to prevent any stdout logging
            logging.getLogger().handlers = []
            # Set root logger level to CRITICAL to minimize any accidental logging
            logging.getLogger().setLevel(logging.CRITICAL)
        else:
            # Ensure logs directory exists
            logs_dir = get_logs_dir()
            logs_dir.mkdir(parents=True, exist_ok=True)
            
            # Configure root logger for debug mode
            root_logger = logging.getLogger()
            root_logger.setLevel(logging.INFO)
            # Add file handler for general logs
            handler = logging.FileHandler(str(logs_dir / "app.log"))
            handler.setFormatter(logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
            root_logger.addHandler(handler)

        asyncio.run(run_server(args))
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()

__all__ = ["main", "server"]
