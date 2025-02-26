#!/usr/bin/env python3
import os
import sys
import asyncio
import argparse
from typing import Dict, List, Tuple

# Import the PerplexityClient from your existing module
from src.mcp_perplexity.perplexity_client import PerplexityClient

async def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Chat with Perplexity API")
    parser.add_argument("--model", type=str, default="sonar-pro", 
                        help="Model to use (default: sonar-pro)")
    parser.add_argument("--message", type=str, required=True,
                        help="Message to send to the API")
    parser.add_argument("--system-prompt", type=str,
                        help="Optional system prompt to use")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug mode to see raw API responses")
    args = parser.parse_args()

    # Check if API key is set
    api_key = os.getenv("PERPLEXITY_API_KEY")
    if not api_key:
        print("Error: PERPLEXITY_API_KEY environment variable is not set", file=sys.stderr)
        sys.exit(1)

    try:
        # Initialize the client
        client = PerplexityClient(api_key=api_key)
        
        # Prepare messages
        messages = []
        
        # Add system prompt if provided
        if args.system_prompt:
            messages.append({"role": "system", "content": args.system_prompt})
            
        # Add user message
        messages.append({"role": "user", "content": args.message})
        
        # Stream the response
        print(f"Sending message to {args.model}...\n", file=sys.stderr)
        print("-" * 50, file=sys.stderr)
        sys.stderr.flush()
        
        # Track citations and usage for final output
        all_citations = []
        final_usage = {}
        
        # Disable output buffering
        sys.stdout.reconfigure(write_through=True) if hasattr(sys.stdout, 'reconfigure') else None
        
        # Use the fixed client's _stream_completion method
        async for content, citations, usage in client._stream_completion(
            messages=messages,
            model=args.model,
        ):
            # Print content chunk directly to stdout and force flush
            sys.stdout.write(content)
            sys.stdout.flush()
            
            # Update citations and usage
            if citations:
                all_citations = citations
            if usage:
                final_usage = usage
                
            # Debug mode: print raw chunks
            if args.debug:
                print(f"\nDEBUG - Received chunk: {repr(content)}", file=sys.stderr)
                if citations:
                    print(f"DEBUG - Citations updated: {citations}", file=sys.stderr)
                if usage:
                    print(f"DEBUG - Usage updated: {usage}", file=sys.stderr)
                sys.stderr.flush()
        
        # Print a separator after the main response
        print("\n\n" + "-" * 50, flush=True)
        
        # Print citations if available
        if all_citations:
            print("\nSources:", flush=True)
            for i, citation in enumerate(all_citations, 1):
                print(f"{i}. {citation}", flush=True)
        
        # Print usage statistics
        if final_usage:
            print("\nAPI Usage:", flush=True)
            print(f"- Prompt tokens: {final_usage.get('prompt_tokens', 'N/A')}", flush=True)
            print(f"- Completion tokens: {final_usage.get('completion_tokens', 'N/A')}", flush=True)
            print(f"- Total tokens: {final_usage.get('total_tokens', 'N/A')}", flush=True)
            
    except Exception as e:
        print(f"Error: {str(e)}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    # Ensure stdout is unbuffered for Python versions that don't support reconfigure
    if not hasattr(sys.stdout, 'reconfigure'):
        # Force unbuffered output
        os.environ['PYTHONUNBUFFERED'] = '1'
    
    asyncio.run(main()) 