# /// script
# dependencies = [
#   "google-antigravity",
#   "ddgs",
#   "httpx"
# ]
# ///

import asyncio
import json
import os
import re
from duckduckgo_search import DDGS
from google.antigravity import Agent, LocalAgentConfig
from google.antigravity.hooks import policy

MD_FILE = "techNL_crawler/techNL_companies.md"
STATE_FILE = "techNL_crawler/companies_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {item["company_name"]: item for item in data}
        except Exception:
            return {}
    return {}

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(state.values()), f, indent=2)

def extract_companies():
    companies = []
    if not os.path.exists(MD_FILE):
        return companies
    with open(MD_FILE, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r'\|\s*\*\*([^*]+)\*\*\s*\|\s*[^|]*\s*\|\s*\[[^\]]*\]\((https?://[^)]+)\)\s*\|', line)
            if m:
                companies.append({"company_name": m.group(1).strip(), "website_url": m.group(2).strip()})
    return companies

async def web_search(query: str) -> str:
    """Performs a web search using DuckDuckGo and returns results."""
    try:
        results = DDGS().text(query, max_results=5)
        if not results:
            return "No results found."
        formatted = []
        for r in results:
            formatted.append(f"Title: {r['title']}\nLink: {r['href']}\nSnippet: {r['body']}")
        return "\n\n".join(formatted)
    except Exception as e:
        return f"Search failed: {e}"

def get_or_refresh_token(force=False):
    global _token_expiry
    import time
    now = int(time.time())
    
    # Check if existing token is valid (expiry is stored or we assume 45 min buffer)
    if not force and _token_expiry > now + 300: # 5 min safety buffer
        try:
            if os.path.exists(_token_file_path):
                with open(_token_file_path, "r", encoding="utf-8") as f:
                    t = f.read().strip()
                    if t:
                        return t
        except Exception:
            pass

    token = None
    key_path = r"C:\Users\Daniel\.gemini\service_account.json"
    
    if os.path.exists(key_path):
        try:
            print(f"Generating dynamic OAuth token using Service Account key from: {key_path}")
            with open(key_path, 'r') as f:
                info = json.load(f)
                
            private_key_pem = info["private_key"]
            client_email = info["client_email"]
            token_uri = info["token_uri"]
            
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import padding
            from cryptography.hazmat.primitives import hashes
            
            private_key = serialization.load_pem_private_key(
                private_key_pem.encode(),
                password=None
            )
            
            iat = int(time.time())
            exp = iat + 3600
            header = {"alg": "RS256", "typ": "JWT"}
            payload = {
                "iss": client_email,
                "sub": client_email,
                "aud": token_uri,
                "iat": iat,
                "exp": exp,
                "scope": "https://www.googleapis.com/auth/generative-language https://www.googleapis.com/auth/cloud-platform"
            }
            
            import base64
            def b64_encode(data):
                return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
                
            unsigned_jwt = f"{b64_encode(header)}.{b64_encode(payload)}"
            
            signature = private_key.sign(
                unsigned_jwt.encode(),
                padding.PKCS1v15(),
                hashes.SHA256()
            )
            
            signed_jwt = f"{unsigned_jwt}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"
            
            import urllib.request
            import urllib.parse
            refresh_data = urllib.parse.urlencode({
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": signed_jwt
            }).encode()
            
            refresh_req = urllib.request.Request(token_uri, data=refresh_data, method="POST")
            with urllib.request.urlopen(refresh_req) as resp:
                res_data = json.loads(resp.read().decode())
                token = res_data.get("access_token")
                _token_expiry = exp
                print("Successfully generated dynamic Service Account OAuth token.")
        except Exception as e:
            print(f"Failed to generate Service Account token: {e}")

    # Fallback 1: Windows gcloud session
    if not token:
        try:
            import subprocess
            result = subprocess.run(
                ["gcloud.cmd", "auth", "print-access-token"],
                capture_output=True,
                text=True,
                check=True
            )
            token = result.stdout.strip()
            _token_expiry = now + 1800 # Assume gcloud tokens last at least 30 mins
            print("Successfully obtained fresh OAuth token from active gcloud session.")
        except Exception as e:
            print(f"gcloud auth token retrieval failed: {e}")
            
    # Fallback 2: ~/.gemini/oauth_creds.json
    if not token:
        try:
            creds_path = os.path.expanduser("~/.gemini/oauth_creds.json")
            if os.path.exists(creds_path):
                with open(creds_path, "r") as f:
                    creds = json.load(f)
                    token = creds.get("access_token")
                    _token_expiry = now + 1800
                    print("Successfully loaded OAuth token from Gemini CLI config.")
        except Exception as e:
            print(f"Could not load OAuth creds from CLI config: {e}")
            
    if not token:
        token = os.environ.get("GEMINI_API_KEY")
        if token:
            _token_expiry = now + 3600
            print("Falling back to pre-existing GEMINI_API_KEY environment variable.")
        else:
            raise RuntimeError("Could not obtain any OAuth credentials (SA key, gcloud, or CLI config).")

    # Write to token file
    os.makedirs(os.path.dirname(_token_file_path), exist_ok=True)
    with open(_token_file_path, "w", encoding="utf-8") as f:
        f.write(token)
        
    return token

_token_file_path = "techNL_crawler/.active_token"
_token_expiry = 0

def setup_oauth_and_sdk():
    """
    Sets up the Google Antigravity SDK environment on Windows to use Google OAuth.
    Generates a dynamic token using the Service Account and starts the WSL proxy.
    """
    import os
    import subprocess
    import time
    import atexit
    
    # Get/generate initial token to write the file
    get_or_refresh_token(force=True)
    
    # Start WSL proxy
    proxy_cmd = [
        "wsl.exe",
        "python3",
        "/mnt/c/Users/Daniel/Documents/development/ai-job-search/wsl_proxy.py",
        "/mnt/c/Users/Daniel/Documents/development/ai-job-search/techNL_crawler/.active_token",
        "50099",
        "ai-job-search-496915"
    ]
    print("Starting WSL proxy inside WSL...")
    proxy_process = subprocess.Popen(
        proxy_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    # Register cleanup on exit
    def cleanup_proxy():
        print("Stopping WSL proxy...")
        proxy_process.terminate()
        try:
            proxy_process.wait(timeout=2)
        except Exception:
            proxy_process.kill()
            
    atexit.register(cleanup_proxy)
    
    # Give the proxy a brief moment to start up and bind to port
    time.sleep(1.5)

    # Set proxy variables for host environment, which will pass to WSL
    os.environ["HTTP_PROXY"] = "http://127.0.0.1:50099"
    os.environ["HTTPS_PROXY"] = "http://127.0.0.1:50099"
    os.environ["NO_PROXY"] = "localhost,127.0.0.1"
    os.environ["no_proxy"] = "localhost,127.0.0.1"
    
    # Set the token in GEMINI_API_KEY to a dummy value so SDK doesn't reject it
    os.environ["GEMINI_API_KEY"] = "dummy"
    
    # Share variables using WSLENV
    os.environ["WSLENV"] = "GEMINI_API_KEY/u:HTTPS_PROXY/u:HTTP_PROXY/u:NO_PROXY/u:no_proxy" + (f":{os.environ['WSLENV']}" if "WSLENV" in os.environ else "")
    
    # Set the wrapper harness path
    os.environ["ANTIGRAVITY_HARNESS_PATH"] = r"C:\Users\Daniel\Documents\development\ai-job-search\wsl_harness.bat"

async def main():
    setup_oauth_and_sdk()

    companies = extract_companies()
    print(f"Found {len(companies)} companies in markdown.")

    state = load_state()
    
    models_fallback = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash"]
    model_idx = 0
    
    for comp in companies:
        name = comp["company_name"]
        url = comp["website_url"]
        
        # Ensure the token is fresh before researching each company
        get_or_refresh_token()
        
        if name not in state or state[name].get("status") == "pending" or not state[name].get("career_page_url"):
            print(f"Mapping Career Page for: {name} ({url})")
            prompt = (
                f"Target Company: {name}\n"
                f"Official Website: {url}\n\n"
                "Use the `web_search` tool to find the official careers/jobs page URL for this specific company. "
                "This could be on their own domain (e.g. /careers or French equivalents like /carrieres) or an ATS (Greenhouse, Lever, etc.).\n"
                "CRITICAL: Always anchor your search queries with the company's domain/website URL (e.g. 'site:domain.com careers', 'domain.com jobs') "
                "to target the correct company and prevent name collision with unrelated, similarly named entities.\n"
                "If you find the career page, output ONLY a JSON object with the key 'career_page_url' and the URL as the value. "
                "If you cannot find it after a search, output ONLY a JSON object with 'career_page_url' as null."
            )
            
            max_retries = 5
            backoff = 10
            for attempt in range(max_retries):
                current_model = models_fallback[model_idx % len(models_fallback)]
                print(f" -> Attempt {attempt + 1} using model: {current_model}")
                
                config = LocalAgentConfig(
                    model=current_model,
                    system_instructions=(
                        "You are an expert web researcher. Your goal is to find the official career/jobs page for the given company. "
                        "Always use domain-anchored queries (site:domain or domain.com) in your searches to resolve the target company accurately."
                    ),
                    tools=[web_search],
                    policies=[policy.allow_all()],
                    workspaces=["/mnt/c/Users/Daniel/Documents/development/ai-job-search"]
                )
                
                try:
                    async with Agent(config) as agent:
                        response = await agent.chat(prompt)
                        # We expect structured-like JSON from the agent, so we accumulate the string
                        full_response = ""
                        async for token in response:
                            full_response += token
                        
                        # Extract JSON from response
                        json_match = re.search(r'\{.*\}', full_response, re.DOTALL)
                        if json_match:
                            data = json.loads(json_match.group(0))
                            career_url = data.get("career_page_url")
                            state[name] = {
                                "company_name": name,
                                "website_url": url,
                                "career_page_url": career_url,
                                "status": "active" if career_url else "failed"
                            }
                            print(f" -> Found: {career_url}")
                            # Succeeded, wait a bit to avoid hitting rate limits for the next request
                            await asyncio.sleep(4)
                            break
                        else:
                            # If we didn't find JSON, let's treat this as a retryable parse failure or rate-limit closure
                            print(f" -> Failed to parse output (no JSON found in: {repr(full_response)}). Retrying in {backoff} seconds...")
                            model_idx += 1  # Rotate model
                            await asyncio.sleep(backoff)
                            backoff *= 2
                except Exception as e:
                    if "1000" in str(e) or "ConnectionClosedOK" in e.__class__.__name__ or "sent 1000" in str(e):
                        # Clean WebSocket close - check if we got a valid response before closure
                        json_match = re.search(r'\{.*\}', full_response, re.DOTALL)
                        if json_match:
                            data = json.loads(json_match.group(0))
                            career_url = data.get("career_page_url")
                            state[name] = {
                                "company_name": name,
                                "website_url": url,
                                "career_page_url": career_url,
                                "status": "active" if career_url else "failed"
                            }
                            print(f" -> Found (via complete trajectory): {career_url}")
                            await asyncio.sleep(4)
                            break
                        else:
                            # The harness closed cleanly but we received no JSON, meaning it likely aborted due to internal rate limit.
                            print(f" -> WebSocket closed cleanly on attempt {attempt + 1} but no valid JSON was accumulated. This indicates an internal rate-limit abort. Retrying in {backoff} seconds...")
                            model_idx += 1  # Rotate model
                            await asyncio.sleep(backoff)
                            backoff *= 2
                    elif "429" in str(e) or "quota" in str(e).lower() or "limit" in str(e).lower():
                        print(f" -> 429 Quota limit hit on attempt {attempt + 1}. Retrying in {backoff} seconds...")
                        model_idx += 1  # Rotate model
                        await asyncio.sleep(backoff)
                        backoff *= 2
                    else:
                        print(f" -> Error during mapping on attempt {attempt + 1}: {e}. Retrying in {backoff} seconds...")
                        model_idx += 1  # Rotate model
                        await asyncio.sleep(backoff)
                        backoff *= 2
            else:
                # If we exhausted all retries
                print(f" -> Failed to map {name} after {max_retries} attempts.")
                state[name] = {"company_name": name, "website_url": url, "status": "failed"}
            
            save_state(state)
        else:
            print(f"Skipping {name} - already mapped.")

if __name__ == "__main__":
    asyncio.run(main())
