import logging
import os
from quart import render_template, jsonify, request
from .database_extension import db
from ..settings import get_settings_with_env_defaults, DEBUG_LOGS, MODEL_PROFILES

# Setup logging
logger = logging.getLogger(__name__)

# Only enable logging if DEBUG_LOGS is set to true
if DEBUG_LOGS:
    logger.setLevel(logging.INFO)
else:
    logger.setLevel(logging.CRITICAL)  # Effectively disable logging


def register_routes(app):
    @app.route('/')
    async def index():
        try:
            page = request.args.get('page', 1, type=int)
            chats = db.get_all_chats(page=page)
            return await render_template('index.html', chats=chats)
        except Exception as e:
            logger.error(f"Error in index route: {e}")
            return await render_template('error.html', error="Failed to load chats"), 500

    @app.route('/chat/<chat_id>')
    async def chat(chat_id):
        try:
            chat = db.get_chat(chat_id)
            if not chat:
                return await render_template('error.html', error="Chat not found"), 404

            messages = db.get_chat_messages(chat_id)
            return await render_template('chat.html', chat=chat, messages=messages)
        except Exception as e:
            logger.error(f"Error in chat route: {e}")
            return await render_template('error.html', error="Failed to load chat"), 500

    @app.route('/api/chats')
    async def api_chats():
        try:
            page = request.args.get('page', 1, type=int)
            chats = db.get_all_chats(page=page)
            # If it's an HTMX request, return the chat list HTML
            if request.headers.get('HX-Request'):
                return await render_template('_chat_list.html', chats=chats)
            # Otherwise return JSON for API consumers
            return jsonify({
                'chats': [{
                    'id': chat['id'],
                    'title': chat['title'],
                    'created_at': chat['created_at'].isoformat()
                } for chat in chats['chats']],
                'pagination': chats['pagination']
            })
        except Exception as e:
            logger.error(f"Error in api_chats route: {e}")
            return jsonify({'error': 'Failed to load chats'}), 500

    @app.route('/api/chat/<chat_id>/messages')
    async def api_chat_messages(chat_id):
        try:
            messages = db.get_chat_messages(chat_id)
            # If it's an HTMX request, return the messages list HTML
            if request.headers.get('HX-Request'):
                return await render_template('_message_list.html', messages=messages)
            # Otherwise return JSON for API consumers
            return jsonify([{
                'id': msg['id'],
                'role': msg['role'],
                'content': msg['content'],
                'timestamp': msg['timestamp'].isoformat()
            } for msg in messages])
        except Exception as e:
            logger.error(f"Error in api_chat_messages route: {e}")
            return jsonify({'error': 'Failed to load messages'}), 500

    @app.route('/api/chat/<chat_id>', methods=['DELETE'])
    async def delete_chat(chat_id):
        try:
            db.delete_chat(chat_id)
            return '', 204
        except Exception as e:
            logger.error(f"Error deleting chat: {e}")
            return jsonify({'error': 'Failed to delete chat'}), 500

    @app.route('/settings')
    async def settings():
        try:
            # Get all settings and override flag
            settings = db.get_all_settings()
            override_env_vars = db.should_override_env_vars()
            
            # Get environment variables to use as defaults
            env_defaults = get_settings_with_env_defaults()
            
            # Get all profiles
            profiles = db.get_all_profiles()
            
            # Get tool profiles
            ask_profile_id = db.get_tool_profile('ask')
            chat_profile_id = db.get_tool_profile('chat')
            
            # Get model profiles from settings
            model_profiles = MODEL_PROFILES
            
            return await render_template('settings.html', 
                                        settings=settings, 
                                        env_defaults=env_defaults,
                                        override_env_vars=override_env_vars,
                                        profiles=profiles,
                                        ask_profile_id=ask_profile_id,
                                        chat_profile_id=chat_profile_id,
                                        model_profiles=model_profiles)
        except Exception as e:
            logger.error(f"Error in settings route: {e}")
            return await render_template('error.html', error="Failed to load settings"), 500

    @app.route('/api/settings', methods=['GET', 'POST'])
    async def api_settings():
        try:
            if request.method == 'GET':
                # Return all settings
                settings = db.get_all_settings()
                return jsonify(settings)
            else:
                # Update settings
                data = await request.get_json()
                
                # Validate and update each setting
                for key, value in data.items():
                    # Skip empty API key to avoid overwriting it
                    if key == 'api_key' and not value:
                        continue
                    
                    # Convert numeric values to strings
                    if isinstance(value, (int, float)):
                        value = str(value)
                        
                    db.set_setting(key, value)
                
                return jsonify({'success': True})
        except Exception as e:
            logger.error(f"Error in api_settings route: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/settings/override', methods=['POST'])
    async def api_settings_override():
        try:
            data = await request.get_json()
            enabled = data.get('enabled', False)
            
            # Toggle environment variable override
            db.toggle_env_override(enabled)
            
            return jsonify({'success': True})
        except Exception as e:
            logger.error(f"Error in api_settings_override route: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/settings/reset', methods=['POST'])
    async def api_settings_reset():
        try:
            # Reset settings to defaults
            db.reset_settings_to_defaults()
            
            return jsonify({'success': True})
        except Exception as e:
            logger.error(f"Error in api_settings_reset route: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/api/profiles', methods=['GET', 'POST'])
    async def api_profiles():
        try:
            if request.method == 'GET':
                # Return all profiles
                profiles = db.get_all_profiles()
                return jsonify(profiles)
            else:
                # Create a new profile
                data = await request.get_json()
                name = data.get('name')
                model = data.get('model')
                temperature = float(data.get('temperature', 0.7))
                top_p = float(data.get('top_p', 0.9))
                system_prompt = data.get('system_prompt', '')
                
                if not name or not model:
                    return jsonify({'error': 'Name and model are required'}), 400
                
                profile_id = db.create_profile(name, model, temperature, top_p, system_prompt)
                if profile_id:
                    return jsonify({'success': True, 'profile_id': profile_id})
                else:
                    return jsonify({'error': 'Failed to create profile'}), 500
        except Exception as e:
            logger.error(f"Error in api_profiles route: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/profiles/<profile_id>', methods=['GET', 'PUT', 'DELETE'])
    async def api_profile(profile_id):
        try:
            if request.method == 'GET':
                # Get a specific profile
                profile = db.get_profile(profile_id)
                if profile:
                    return jsonify(profile)
                else:
                    return jsonify({'error': 'Profile not found'}), 404
            
            elif request.method == 'PUT':
                # Update a profile
                data = await request.get_json()
                name = data.get('name')
                model = data.get('model')
                temperature = float(data.get('temperature', 0.7))
                top_p = float(data.get('top_p', 0.9))
                system_prompt = data.get('system_prompt', '')
                
                if not name or not model:
                    return jsonify({'error': 'Name and model are required'}), 400
                
                success = db.update_profile(profile_id, name, model, temperature, top_p, system_prompt)
                if success:
                    return jsonify({'success': True})
                else:
                    return jsonify({'error': 'Failed to update profile'}), 500
            
            elif request.method == 'DELETE':
                # Delete a profile
                success = db.delete_profile(profile_id)
                if success:
                    return jsonify({'success': True})
                else:
                    return jsonify({'error': 'Cannot delete default profiles'}), 400
        except Exception as e:
            logger.error(f"Error in api_profile route: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/tool-profiles', methods=['POST'])
    async def api_tool_profiles():
        try:
            data = await request.get_json()
            tool_name = data.get('tool_name')
            profile_id = data.get('profile_id')
            
            if not tool_name or profile_id is None:
                return jsonify({'error': 'Tool name and profile ID are required'}), 400
            
            success = db.set_tool_profile(tool_name, profile_id)
            if success:
                return jsonify({'success': True})
            else:
                return jsonify({'error': 'Failed to set tool profile'}), 500
        except Exception as e:
            logger.error(f"Error in api_tool_profiles route: {e}")
            return jsonify({'error': str(e)}), 500
